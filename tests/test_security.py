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


class ValidatePasswordTestCase(unittest.TestCase):
    def test_accepts_password_within_length_bounds(self):
        security.validate_password("hunter2")  # must not raise

    def test_rejects_too_short(self):
        with self.assertRaises(security.SecurityError):
            security.validate_password("abc")

    def test_rejects_too_long(self):
        with self.assertRaises(security.SecurityError):
            security.validate_password("a" * 65)

    def test_accepts_boundary_lengths(self):
        security.validate_password("a" * 4)
        security.validate_password("a" * 64)

    def test_rejects_embedded_newline(self):
        # A newline written verbatim into a `key=value` wayvnc config line
        # would inject an extra config line -- e.g. "x\nenable_pam=true".
        with self.assertRaises(security.SecurityError):
            security.validate_password("goodpass\nenable_pam=true")

    def test_rejects_other_control_characters(self):
        with self.assertRaises(security.SecurityError):
            security.validate_password("good\tpass")


class ValidateUsernameTestCase(unittest.TestCase):
    def test_accepts_username_within_length_bounds(self):
        security.validate_username("remote")  # must not raise

    def test_rejects_empty(self):
        with self.assertRaises(security.SecurityError):
            security.validate_username("")

    def test_rejects_too_long(self):
        with self.assertRaises(security.SecurityError):
            security.validate_username("a" * 65)

    def test_accepts_boundary_lengths(self):
        security.validate_username("a")
        security.validate_username("a" * 64)

    def test_rejects_embedded_newline(self):
        with self.assertRaises(security.SecurityError):
            security.validate_username("someone\npassword=hijacked")


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
        security.write_wayvnc_config(self.config_path, self.cert_path, self.key_path,
                                      "someone", "hunter2")
        text = self.config_path.read_text()
        self.assertIn("enable_auth=true", text)
        self.assertIn(f"certificate_file={self.cert_path}", text)
        self.assertIn(f"private_key_file={self.key_path}", text)
        self.assertIn("username=someone", text)
        self.assertIn("password=hunter2", text)

    def test_config_file_is_private(self):
        security.write_wayvnc_config(self.config_path, self.cert_path, self.key_path,
                                      "someone", "hunter2")
        mode = stat.S_IMODE(self.config_path.stat().st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
