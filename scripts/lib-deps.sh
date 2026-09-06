# Shared dependency helpers for install-daemon.sh.
# Source this, don't run it directly.

# wayvnc isn't part of a base Omarchy install -- confirmed live: a fresh
# machine has hyprctl but not wayvnc, and the daemon fails every `start`
# with an opaque "No such file or directory: 'wayvnc'" until it's installed
# by hand. Installing it here (mirroring the ufw allow-rule pattern: try,
# and print the exact manual command if sudo can't run non-interactively)
# is what makes "one command, then click Set up now" actually true instead
# of silently depending on a step nothing ever asks for.
tablet_display_ensure_wayvnc() {
  if command -v wayvnc >/dev/null 2>&1; then
    return 0
  fi
  echo "kdm.tablet-display: wayvnc not found, installing..."
  if command -v pacman >/dev/null 2>&1 \
      && sudo pacman -S --noconfirm --needed wayvnc >/dev/null 2>&1; then
    echo "kdm.tablet-display: installed wayvnc."
    return 0
  fi
  echo "kdm.tablet-display: could not install wayvnc automatically (needs sudo, or pacman isn't available)." >&2
  echo "  Install it yourself, then click \"Set up now\" again (or re-run this script):" >&2
  echo "    sudo pacman -S wayvnc" >&2
}
