# Shared dependency helpers for install-daemon.sh.
# Source this, don't run it directly.

# wayvnc isn't part of a base Omarchy install -- confirmed live: a fresh
# machine has hyprctl but not wayvnc, and the daemon fails every `start`
# with an opaque "No such file or directory: 'wayvnc'" until it's installed
# by hand. Installing it here via tablet_display_sudo (see lib-privilege.sh)
# is what makes "one command, then click Set up now" actually true instead
# of silently depending on a step nothing ever asks for.
tablet_display_ensure_wayvnc() {
  if command -v wayvnc >/dev/null 2>&1; then
    return 0
  fi
  echo "kdm.tablet-display: wayvnc not found, installing..."
  if command -v pacman >/dev/null 2>&1 \
      && tablet_display_sudo pacman -S --noconfirm --needed wayvnc >/dev/null 2>&1; then
    echo "kdm.tablet-display: installed wayvnc."
    return 0
  fi
  echo "kdm.tablet-display: could not install wayvnc automatically (pacman isn't available, or the prompt was dismissed/failed)." >&2
  echo "  Install it yourself, then click \"Set up now\" again (or re-run this script):" >&2
  echo "    sudo pacman -S wayvnc" >&2
}
