"""Self-signed TLS cert + password setup for wayvnc's built-in encryption.

wayvnc has no CLI flags for this -- confirmed via its own docs and live
testing -- only a config file (`enable_auth=true` plus certificate_file,
private_key_file, and password, all required together) offers it, via
VeNCrypt/RSA-AES security types (confirmed live with a raw RFB handshake:
security types 19/129/5 offered instead of plain/VNC-Auth). The cert, key,
and password are generated once and reused across sessions; only the wayvnc
config file itself is rewritten per-session (its cert/key/password lines
never change, only whether it exists at all reflects the current setting).
"""

from __future__ import annotations

import secrets
import subprocess
from pathlib import Path

from . import config

OPENSSL = "openssl"

# Excludes visually ambiguous characters (0/O, 1/l/I) -- this password gets
# read off a screen and typed into a mobile VNC client by hand.
_PASSWORD_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz"
_PASSWORD_LENGTH = 10


class SecurityError(RuntimeError):
    pass


def generate_password() -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))


def _reject_control_characters(value: str, field: str) -> None:
    """A newline in a value written verbatim into a `key=value` wayvnc
    config line would inject an extra config line -- e.g. a password of
    "x\\nenable_pam=true" would silently turn PAM on. Every value that ends
    up in that file goes through this, whether user-chosen or generated."""
    if any(ord(ch) < 0x20 for ch in value):
        raise SecurityError(f"{field} cannot contain control characters or newlines")


def validate_password(password: str) -> None:
    """Raises SecurityError for a password wayvnc/the user would regret.

    No character-set restriction beyond control characters: unlike the
    auto-generated password, a custom one is never read off a screen
    character-by-character, so ambiguous characters are not a usability
    problem here. Length is checked -- long enough to be worth having,
    short enough that VNC clients with old fixed-width password fields
    still accept it.
    """
    if not isinstance(password, str) or not (
        config.MIN_PASSWORD_LENGTH <= len(password) <= config.MAX_PASSWORD_LENGTH
    ):
        raise SecurityError(
            f"password must be {config.MIN_PASSWORD_LENGTH}-{config.MAX_PASSWORD_LENGTH} characters"
        )
    _reject_control_characters(password, "password")


def validate_username(username: str) -> None:
    if not isinstance(username, str) or not (
        config.MIN_USERNAME_LENGTH <= len(username) <= config.MAX_USERNAME_LENGTH
    ):
        raise SecurityError(
            f"username must be {config.MIN_USERNAME_LENGTH}-{config.MAX_USERNAME_LENGTH} characters"
        )
    _reject_control_characters(username, "username")


def ensure_cert(cert_path: Path, key_path: Path) -> None:
    """Generates a self-signed cert/key pair if one doesn't already exist.

    A stable, long-lived self-signed cert is normal for LAN VNC use --
    RealVNC Viewer and other clients show a one-time "unknown certificate"
    prompt the user accepts, the same as any self-managed home-network
    device. Regenerating it every session would just make that prompt
    reappear every time for no security benefit.
    """
    if cert_path.exists() and key_path.exists():
        return
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key_path), "-out", str(cert_path),
                "-days", "3650", "-subj", "/CN=kdm-tablet-display",
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SecurityError(f"could not generate a TLS certificate: {exc.stderr}") from exc
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SecurityError(f"could not generate a TLS certificate: {exc}") from exc
    key_path.chmod(0o600)


def write_wayvnc_config(config_path: Path, cert_path: Path, key_path: Path,
                         username: str, password: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "enable_auth=true\n"
        f"certificate_file={cert_path}\n"
        f"private_key_file={key_path}\n"
        f"username={username}\n"
        f"password={password}\n"
    )
    config_path.chmod(0o600)
