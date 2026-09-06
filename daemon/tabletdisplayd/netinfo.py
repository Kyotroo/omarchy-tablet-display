"""LAN address discovery and ephemeral port allocation.

Tablet mirroring is explicitly LAN-only for v1 (no Tailscale/VPN interfaces),
so the address handed to the QR code has to be the interface the local
network actually reaches, not just any interface with a routable address.
"""

from __future__ import annotations

import re
import socket
import subprocess

_SRC_RE = re.compile(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)")


class NoLanAddressError(RuntimeError):
    pass


def lan_ip() -> str:
    """Return the IP address of the interface holding the default route.

    Uses `ip route get`, a pure routing-table lookup answered entirely by the
    kernel -- it does not send any packet onto the network, unlike an actual
    connection attempt to 1.1.1.1.
    """
    try:
        result = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise NoLanAddressError(f"could not query default route: {exc}") from exc

    match = _SRC_RE.search(result.stdout)
    if not match:
        raise NoLanAddressError("no default route with a source address")
    return match.group(1)


def free_tcp_port(preferred: int | None = None) -> int:
    """Return a free TCP port, trying `preferred` first if given."""
    candidates = [preferred] if preferred else []
    candidates.append(0)
    last_error = None
    for candidate in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("0.0.0.0", candidate or 0))
                return probe.getsockname()[1]
        except OSError as exc:
            last_error = exc
            continue
    raise NoLanAddressError(f"could not allocate a TCP port: {last_error}")
