"""QR code generation via the `qrencode` CLI (already an Omarchy/Arch package,
no Python dependency needed)."""

from __future__ import annotations

import subprocess

QRENCODE = "qrencode"


class QrEncodeError(RuntimeError):
    pass


def generate_png(data: str, size: int = 6) -> bytes:
    try:
        result = subprocess.run(
            [QRENCODE, "-o", "-", "-t", "PNG", "-s", str(size), "--", data],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise QrEncodeError(f"qrencode failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise QrEncodeError(f"qrencode failed: {detail}")
    return result.stdout
