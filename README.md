# Tablet Display

Turn a tablet — or any other device with a VNC client, including another PC —
into a wireless second monitor for Omarchy over your LAN. Click the bar
toggle, scan a QR code, connect. No manual Hyprland config, no running
`wayvnc` from a terminal.

## What it does

- **One bar icon**, click to open a panel showing a QR code and the raw
  connection address.
- **Auto-configuring resolution**: the tablet's browser reports its real
  screen size once, and the virtual display is resized to match — no
  guessing a resolution ahead of time.
- **Extend or Duplicate**: use the tablet as a genuine second monitor with
  its own workspace (Extend, the default), or mirror your current screen
  onto it (Duplicate).
- **Position**: tell Hyprland which side of your main screen the new
  display sits on (left/right/above/below), so window/workspace movement
  between screens goes the direction you'd expect.
- **Optional encryption**: a toggle turns on TLS + a username/password for
  the VNC session (self-signed certificate, generated once). Off by
  default, matching a plain LAN-only setup.
- **Resolution presets**: a small bundled list of tablets to pick from
  instead of relying on auto-detection, for cases like split-screen mode or
  a forced OS zoom level throwing the auto-detected size off.

## Install

Not yet published to a git remote or the marketplace. Once it is:

```bash
omarchy plugin add <this repo's URL> --enable
```

For now, testing this checkout locally means placing (or symlinking) this
directory at `~/.config/omarchy/plugins/kdm.tablet-display/`, then:

```bash
bash scripts/install-daemon.sh
omarchy plugin enable kdm.tablet-display --section right
```

The installer detects `ufw` if present, works out your LAN subnet from the
system's own default route, and adds a scoped allow rule for the setup page
and VNC ports (LAN only, never the whole internet) — the single most common
reason this kind of thing silently fails to connect. See
[Firewall](#firewall) below if you use a different firewall.

## Quick start

1. Click the bar icon. The panel opens and a session starts.
2. Scan the QR code with the tablet (or open the printed address in its
   browser).
3. The page detects the tablet's screen, reports it back, and offers a
   button to open the address directly in a VNC app — installing one first
   if needed (defaults to [RealVNC
   Viewer](https://www.realvnc.com/en/connect/download/viewer/), available
   free on Android, iOS, Windows, macOS, and Linux).
4. Click the bar icon again (or the Stop button in the panel) to tear
   everything down cleanly.

## Using another PC to control this one

VNC's remote-input handling was never turned off for the tablet use case,
so this already works today with no extra setup: point a desktop VNC
client (RealVNC Viewer, or any RFB-compatible client) at the address shown
in the panel, using **Duplicate** mode so it's viewing/controlling your
actual desktop rather than a separate virtual one. Mouse and keyboard input
from that PC reaches this machine exactly like a local input device would.

This is standard VNC remote-desktop behavior, not something specific to
tablets — the tablet-oriented parts of this plugin (the QR code, the
mobile app store detection/links) are just a convenience layer on top of
the same underlying session.

**A real limitation to know about**: VNC (the RFB protocol) has no concept
of touch input — only a mouse pointer position and buttons. If the
*controlling* PC has a touchscreen, touching it will feel like an awkward
trackpad rather than real touch, because the OS is translating touch into
synthetic mouse events before the VNC client ever sees it. There's no
setting on this plugin's side that changes that — it's a property of the
protocol itself. Some mobile VNC apps offer a "touch vs. trackpad" mode in
their own settings; most desktop clients don't.

## Settings

All of these live in the panel and take effect immediately if a session is
already running (a brief VNC disconnect/reconnect while the change
applies, since wayvnc has no way to change these without restarting):

| Setting | Notes |
| --- | --- |
| Resolution preset | Overrides auto-detection; pick "Auto-detected" to go back to reading the tablet's own reported size. |
| Display mode | **Extend** (default): a real second monitor with its own workspace. **Duplicate**: mirrors whichever screen is currently focused — no separate workspace, since it isn't a separate display. |
| Position | Where the new display sits relative to your main screen. Only meaningful in Extend mode. |
| Secure connection | Off by default (plain, unauthenticated — fine for a trusted home LAN). On: TLS + username/password, self-signed certificate generated once and reused. |
| Username / Password | Editable once encryption is on. A random password is generated the first time you enable encryption; use Regenerate for a new random one, or type your own. |

## Firewall

wayvnc and the setup page are intentionally unauthenticated unless you turn
on the Secure connection setting, so they're scoped to your LAN by the
firewall rule the installer adds (`ufw`, if present) rather than by the
plugin itself. If you use a different firewall, or `ufw` wasn't active at
install time, allow inbound TCP on the setup port (5810 by default) and the
VNC port (5900 by default) from your LAN subnet.

## Known limitations

- **LAN only.** No remote/off-network access (e.g. via Tailscale) in this
  version.
- **One tablet at a time.** Starting a new session while one is already
  running is a no-op, not a second simultaneous display.
- **No touch-to-input passthrough design.** The tablet is a display (and,
  incidentally, a full VNC client with mouse/keyboard-equivalent input from
  its VNC app) — not a Sidecar-style touch-aware input device. See the
  touch/trackpad note above.
- **DPMS / screen lock**: wayvnc sessions have been reported elsewhere to
  drop about 30 seconds after the physical screen locks. The daemon
  auto-respawns wayvnc if it exits unexpectedly, so a dropped session
  should reconnect on its own rather than needing a manual restart, but a
  locked screen briefly interrupting the session is a known rough edge, not
  fully eliminated.

## Uninstall

Omarchy's plugin system only does file operations on removal (disable,
delete/unlink) — it has no hook to run a plugin's own cleanup script
automatically, so that has to happen first, while the plugin directory
(and its scripts) still exist:

```bash
omarchy plugin disable kdm.tablet-display
bash ~/.config/omarchy/plugins/kdm.tablet-display/scripts/uninstall-daemon.sh
omarchy plugin remove kdm.tablet-display --yes
```

The middle step stops and removes the systemd unit, sockets, generated
certificate, and the `ufw` rule the installer added. Unlike some plugins,
there's no user-authored data to preserve on removal — presets.json is a
bundled asset, not something you edit.

## Development

```bash
bash tests/run.sh
```

Runs the full test suite: the daemon's state machine and IPC protocol
against fake `hyprctl`/`wayvnc`/`ip`/`openssl` fixtures, the embedded setup
HTTP server, the setup page's platform-detection logic (under a mocked
DOM, via Node), and the firewall subnet-detection helper.

## License

[MIT](LICENSE) © kdm
