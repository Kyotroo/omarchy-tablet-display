"""Paths and defaults shared across the daemon.

Runtime/state paths are resolved through the environment (never hardcoded)
so tests can point the whole daemon at a throwaway HOME/XDG_RUNTIME_DIR the
same way kdm.presets' test harness does.
"""

from __future__ import annotations

import os
from pathlib import Path

PLUGIN_ID = "kdm.tablet-display"
SERVICE_NAME = "kdm-tablet-displayd"

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_REFRESH = 60.0
DEFAULT_VNC_PORT = 5900
DEFAULT_SETUP_PORT = 5810
DEFAULT_POSITION = "auto-right"
DEFAULT_DISPLAY_MODE = "extend"
# A marketplace security review correctly flagged an earlier opt-in design
# (encryption off by default) as exposing an unauthenticated remote desktop
# to the whole LAN subnet the moment a session starts, not just the
# intended tablet. TLS + a password is now unconditional -- there is no
# setting to turn it back off.
# Not a secret -- the password is what protects the connection -- but some
# VNC clients (confirmed with RealVNC Viewer) prompt for a username even
# though wayvnc defaults to an empty one, and leaving that blank is not
# obvious on a mobile keyboard. Always shown, user-editable like the
# password (see set_username), just with a sane default so nobody has to
# set it before their first connection.
DEFAULT_VNC_USERNAME = "remote"
MIN_PASSWORD_LENGTH = 4
MAX_PASSWORD_LENGTH = 64
MIN_USERNAME_LENGTH = 1
MAX_USERNAME_LENGTH = 64

# Bounds for the tablet's self-reported screen size (setup_server.py) and
# the physical pixel count it produces once multiplied by devicePixelRatio
# (service.py's _handle_client_report). A marketplace security review found
# the old bounds (16000 per axis, DPR up to 8) let an unauthenticated LAN
# peer force repeated 128000x128000 monitor-mode requests. These are still
# generous for any real device -- the point is a firm ceiling, not
# accommodating a hypothetical display no tablet actually has.
MAX_REPORT_CSS_DIMENSION = 8000
MAX_REPORT_DPR = 4.0
MAX_PHYSICAL_DIMENSION = 7680

# Hyprland's own vocabulary for hl.monitor()'s `position`, confirmed live
# (each places the new output relative to whatever else is already placed).
VALID_POSITIONS = ("auto-left", "auto-right", "auto-up", "auto-down")

# "extend": an independent headless output this daemon creates and owns,
# with its own workspace(s), same as any real second monitor in Hyprland --
# there is no separate "extend" distinct from this; every monitor already
# gets its own workspace.
# "mirror": wayvnc captures the currently-focused real monitor directly, no
# headless output involved at all -- confirmed live that a Hyprland
# `mirror` output has no independent Wayland surface wayvnc can bind to.
VALID_DISPLAY_MODES = ("extend", "mirror")


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    return Path(value)


def state_dir() -> Path:
    value = os.environ.get("XDG_STATE_HOME")
    base = Path(value) if value else Path.home() / ".local" / "state"
    return base / "omarchy" / "kdm-tablet-display"


def socket_path() -> Path:
    return runtime_dir() / f"{SERVICE_NAME}.sock"


def wayvnc_control_socket_path() -> Path:
    return runtime_dir() / f"{SERVICE_NAME}-wayvnc.sock"


def session_file_path() -> Path:
    return state_dir() / "session.json"


def settings_file_path() -> Path:
    return state_dir() / "settings.json"


def setup_page_path() -> Path:
    """Path to the bundled static setup page, relative to this checkout --
    never installed elsewhere, so plugin files stay self-contained."""
    return Path(__file__).resolve().parent.parent / "setup_page" / "index.html"
