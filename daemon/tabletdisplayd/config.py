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
