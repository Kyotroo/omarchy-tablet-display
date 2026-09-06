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


def set_monitor_mode(name: str, width: int, height: int, refresh: float | None = None,
                      scale: float = 1.0, position: str = "auto") -> None:
    """Apply a resolution (and scale) to an existing output via hl.monitor.

    `hyprctl keyword monitor ...` is rejected by this compositor's Lua-based
    config ("keyword can't work with non-legacy parsers"); `hl.monitor` is the
    same call monitors.lua makes at parse time, exposed for runtime use via
    `hyprctl eval`.

    `scale` matters once a real device has reported its devicePixelRatio: the
    resolution applied at that point is the tablet's *physical* pixel count
    (CSS `screen.width` * dpr), so setting scale=1 unconditionally would
    render every UI element at native-pixel size -- illegibly small on a
    high-DPI tablet panel. Passing the same dpr back as Hyprland's output
    scale keeps on-screen UI a sensible logical size, the same relationship
    Omarchy's own monitors.lua uses for a real high-DPI display.
    """
    mode = f"{width}x{height}"
    if refresh:
        mode = f"{mode}@{refresh:g}"
    lua = (
        "hl.monitor({output=%s, mode=%s, position=%s, scale=%s, mirror=\"none\"})"
        % (json.dumps(name), json.dumps(mode), json.dumps(position), json.dumps(round(scale, 3)))
    )
    _run(["eval", lua])


def set_monitor_mirror(name: str, source: str, width: int, height: int,
                        refresh: float | None = None) -> None:
    """Mirror `source` onto `name` via hl.monitor's `mirror` field.

    `width`/`height` must be the source's own current mode, passed
    explicitly alongside `mirror` in the same call. Confirmed live this is
    required: `mirror` alone (omitting `mode`) leaves the target at
    whatever mode it already had -- e.g. a fresh headless output's default
    1920x1080 -- instead of the source's actual resolution, even though
    `mirrorOf` correctly reports the mirror is active either way. Only
    passing both together produces a true full-resolution mirror.

    Confirmed live: passing `mirror` at all makes it sticky -- a later
    hl.monitor call that omits the field entirely does NOT clear a mirror
    already in effect, it just leaves it as-is. `set_monitor_mode` above
    always passes `mirror="none"` explicitly for exactly this reason: it is
    the only way back to independent (extend) mode once mirroring has been
    set, not merely "not asking for mirroring".
    """
    mode = f"{width}x{height}"
    if refresh:
        mode = f"{mode}@{refresh:g}"
    lua = (
        "hl.monitor({output=%s, mode=%s, mirror=%s})"
        % (json.dumps(name), json.dumps(mode), json.dumps(source))
    )
    _run(["eval", lua])


def focused_monitor_name() -> str | None:
    """The monitor Hyprland currently considers focused, or None if none is.

    Used to pick a mirror source: "the screen you're looking at" is a more
    sensible default than guessing which connector is "the real" one on a
    machine that might have several physical monitors.
    """
    for monitor in monitors():
        if monitor.get("focused"):
            return monitor["name"]
    return None
