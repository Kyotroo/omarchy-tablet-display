"""Tests for security.py: password generation and the self-signed cert /
wayvnc config plumbing behind the encryption toggle. Uses the fake openssl
fixture (tests/fixtures/bin/openssl) so these stay fast and don't need a
real keypair generated on every run.
"""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "daemon"))

from tabletdisplayd import security  # noqa: E402


class PasswordTestCase(unittest.TestCase):
    def test_generated_password_has_expected_length(self):
        self.assertEqual(len(security.generate_password()), security._PASSWORD_LENGTH)

    def test_generated_passwords_are_not_all_identical(self):
        passwords = {security.generate_password() for _ in range(20)}
        self.assertGreater(len(passwords), 1)

    def test_password_excludes_visually_ambiguous_characters(self):
        password = security.generate_password() * 50  # enough samples
        for char in "0O1lI":
            self.assertNotIn(char, password)


class CertTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ["TEST_TMP_DIR"]))
        self.cert_path = self.tmp / "cert.pem"
        self.key_path = self.tmp / "key.pem"

    def test_ensure_cert_creates_files(self):
        security.ensure_cert(self.cert_path, self.key_path)
        self.assertTrue(self.cert_path.exists())
        self.assertTrue(self.key_path.exists())

    def test_ensure_cert_key_is_private(self):
        security.ensure_cert(self.cert_path, self.key_path)
        mode = stat.S_IMODE(self.key_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_ensure_cert_is_idempotent(self):
        security.ensure_cert(self.cert_path, self.key_path)
        original = self.key_path.read_text()
        self.key_path.write_text("unchanged marker")  # prove a 2nd call is a no-op
        security.ensure_cert(self.cert_path, self.key_path)
        self.assertEqual(self.key_path.read_text(), "unchanged marker")
        self.assertNotEqual(original, "unchanged marker")  # sanity: fixture actually wrote something first time


class WriteWayvncConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=os.environ["TEST_TMP_DIR"]))
        self.cert_path = self.tmp / "cert.pem"
        self.key_path = self.tmp / "key.pem"
        self.config_path = self.tmp / "wayvnc-secure.conf"

    def test_config_contains_required_wayvnc_keywords(self):
        security.write_wayvnc_config(self.config_path, self.cert_path, self.key_path, "hunter2")
        text = self.config_path.read_text()
        self.assertIn("enable_auth=true", text)
        self.assertIn(f"certificate_file={self.cert_path}", text)
        self.assertIn(f"private_key_file={self.key_path}", text)
        self.assertIn("password=hunter2", text)

    def test_config_file_is_private(self):
        security.write_wayvnc_config(self.config_path, self.cert_path, self.key_path, "hunter2")
        mode = stat.S_IMODE(self.config_path.stat().st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
