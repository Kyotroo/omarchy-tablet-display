# Shared firewall helpers for install-daemon.sh / uninstall-daemon.sh.
# Source this, don't run it directly. Requires tablet_display_sudo from
# lib-privilege.sh to already be sourced (both callers do this).
#
# wayvnc and the setup page are unauthenticated by design (LAN-only v1), so
# they need an explicit inbound allow -- a default-deny firewall (ufw is
# active by default on this reference machine) silently drops every
# connection attempt from the tablet otherwise, which is indistinguishable
# from "it's just slow" until you go looking. Scoped to the LAN subnet the
# default route is actually on, not "Anywhere": a laptop that later joins a
# public network should not carry an open unauthenticated VNC port with it.

TABLET_DISPLAY_SETUP_PORT=5810
TABLET_DISPLAY_VNC_PORT=5900
TABLET_DISPLAY_FW_COMMENT="kdm.tablet-display: setup page + VNC"

# Prints the CIDR of the subnet the default route's own interface is on, or
# nothing (and a non-zero exit) if that can't be determined. Deliberately
# keyed off the *default route's* device rather than every local route, so a
# Docker bridge or a Tailscale interface (both out of scope for this plugin)
# can't be mistaken for the LAN.
tablet_display_lan_subnet() {
  local dev
  dev=$(ip -4 route show default 2>/dev/null \
    | grep -oP '(?<=dev )\S+' | head -1)
  [[ -n $dev ]] || return 1
  ip -4 route show dev "$dev" scope link 2>/dev/null \
    | awk '{print $1}' | head -1
}

# Idempotent: ufw itself no-ops (with a message) on an exact duplicate rule.
tablet_display_firewall_allow() {
  if ! command -v ufw >/dev/null 2>&1; then
    return 0
  fi
  local subnet
  if ! subnet=$(tablet_display_lan_subnet) || [[ -z $subnet ]]; then
    echo "kdm.tablet-display: could not determine the LAN subnet automatically." >&2
    echo "  If ufw (or another firewall) blocks incoming connections, allow TCP" >&2
    echo "  $TABLET_DISPLAY_SETUP_PORT and $TABLET_DISPLAY_VNC_PORT from your LAN manually." >&2
    return 0
  fi
  if tablet_display_sudo ufw allow from "$subnet" to any \
      port "$TABLET_DISPLAY_SETUP_PORT,$TABLET_DISPLAY_VNC_PORT" proto tcp \
      comment "$TABLET_DISPLAY_FW_COMMENT" >/dev/null 2>&1; then
    echo "kdm.tablet-display: allowed TCP $TABLET_DISPLAY_SETUP_PORT,$TABLET_DISPLAY_VNC_PORT from $subnet in ufw."
  else
    echo "kdm.tablet-display: could not add the ufw rule automatically (prompt was dismissed/failed)." >&2
    echo "  Run this yourself if the tablet can't connect:" >&2
    echo "    sudo ufw allow from $subnet to any port $TABLET_DISPLAY_SETUP_PORT,$TABLET_DISPLAY_VNC_PORT proto tcp comment '$TABLET_DISPLAY_FW_COMMENT'" >&2
  fi
}

# Removes every ufw rule carrying our comment, not just one -- a rule added
# by an earlier version of this script (before the comment existed, or under
# a different subnet after a network change) should not survive an uninstall
# as orphaned firewall state.
tablet_display_firewall_remove() {
  if ! command -v ufw >/dev/null 2>&1; then
    return 0
  fi
  local removed_any=0
  while true; do
    local line number
    line=$(tablet_display_sudo ufw status numbered 2>/dev/null \
      | grep -F "$TABLET_DISPLAY_FW_COMMENT" | head -1)
    [[ -n $line ]] || break
    number=$(grep -oP '(?<=^\[)[0-9]+(?=\])' <<<"$line")
    [[ -n $number ]] || break
    tablet_display_sudo ufw --force delete "$number" >/dev/null 2>&1 || break
    removed_any=1
  done
  if [[ $removed_any == 1 ]]; then
    echo "kdm.tablet-display: removed its ufw rule(s)."
  fi
}
