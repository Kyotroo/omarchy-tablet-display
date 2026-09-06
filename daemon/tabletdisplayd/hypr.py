"""Thin wrapper around hyprctl for headless output lifecycle.

Two Hyprland quirks this machine's Lua-based config surfaces (see project
memory `hyprland_lua_config_quirks`) drive the choices here:

  * `hyprctl keyword monitor ...` is rejected outright ("keyword can't work
    with non-legacy parsers"). Runtime resolution changes go through
    `hyprctl eval 'hl.monitor({...})'` instead, calling the same Lua function
    monitors.lua itself uses at parse time.
  * A freshly created headless output is *not* guaranteed to be named
    "HEADLESS-1" -- Hyprland keeps incrementing the counter for the life of
    the compositor session, so a second create in the same session can come
    back "HEADLESS-2" even after the first was removed. Callers must diff the
    monitor list before/after `output create headless` to learn the real name
    rather than assuming it.
"""

from __future__ import annotations

import json
import subprocess

HYPRCTL = "hyprctl"


class HyprctlError(RuntimeError):
    pass


def _run(args, timeout=5):
    try:
        result = subprocess.run(
            [HYPRCTL, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HyprctlError(f"hyprctl not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HyprctlError(f"hyprctl timed out: {' '.join(args)}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise HyprctlError(f"hyprctl {' '.join(args)} failed: {detail}")
    return result.stdout


def monitors() -> list[dict]:
    """Return hyprctl's current monitor list (active outputs only)."""
    raw = _run(["monitors", "-j"])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HyprctlError(f"could not parse monitor list: {exc}") from exc


def monitor_names() -> set[str]:
    return {m["name"] for m in monitors()}


def create_headless_output() -> str:
    """Create a headless output and return the name Hyprland assigned it.

    Diffs the monitor list before/after instead of assuming "HEADLESS-1":
    the counter is never reused within a compositor session, so a plugin
    that has already created and removed one headless output this session
    will see "HEADLESS-2" or higher on the next create.
    """
    before = monitor_names()
    _run(["output", "create", "headless"])
    after = monitor_names()
    created = after - before
    if len(created) != 1:
        raise HyprctlError(
            f"expected exactly one new output after create, saw {sorted(created)}"
        )
    return next(iter(created))


def remove_output(name: str) -> None:
    """Remove a headless output. Idempotent: a missing output is not an error."""
    try:
        _run(["output", "remove", name])
    except HyprctlError as exc:
        if "not found" in str(exc).lower():
            return
        raise


def set_monitor_mode(name: str, width: int, height: int, refresh: float | None = None) -> None:
    """Apply a resolution to an existing output via hl.monitor (Lua config path).

    `hyprctl keyword monitor ...` is rejected by this compositor's Lua-based
    config ("keyword can't work with non-legacy parsers"); `hl.monitor` is the
    same call monitors.lua makes at parse time, exposed for runtime use via
    `hyprctl eval`.
    """
    mode = f"{width}x{height}"
    if refresh:
        mode = f"{mode}@{refresh:g}"
    lua = (
        "hl.monitor({output=%s, mode=%s, position=\"auto\", scale=1})"
        % (json.dumps(name), json.dumps(mode))
    )
    _run(["eval", lua])
