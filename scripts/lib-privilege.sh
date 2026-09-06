# Shared privilege-escalation helper for install-daemon.sh /
# uninstall-daemon.sh. Source this, don't run it directly.
#
# Omarchy runs its own polkit authentication agent (registered at shell
# startup -- see `omarchy.polkit` / polkit/PolkitAgent.qml, and the same
# pattern the Tailscale panel plugin uses for its own privileged calls).
# `pkexec` routes through that agent's GUI password prompt over D-Bus, so
# it works identically whether it's run from an interactive terminal or
# from the panel's "Set up now" button -- which runs this whole script as
# a backgrounded Quickshell Process with no terminal attached at all.
# `sudo` has no such fallback: confirmed live, a `sudo` call from that
# backgrounded process fails immediately and silently
# ("pam_unix(sudo:auth): conversation failed") with no prompt of any kind.
# Falling back to `sudo` here only covers the unlikely case of a system
# with no polkit agent at all (still fine from an interactive terminal).
tablet_display_sudo() {
  if command -v pkexec >/dev/null 2>&1; then
    pkexec "$@"
  else
    sudo "$@"
  fi
}
