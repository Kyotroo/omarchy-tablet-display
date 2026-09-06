#!/bin/bash
# Tests tablet_display_lan_subnet against a fake `ip` (see fixtures/bin/ip).
# Does not exercise the actual `sudo ufw` calls -- those touch real system
# firewall state and are verified manually/live instead.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../scripts/lib-firewall.sh
source "$repo_dir/scripts/lib-firewall.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }

# -- picks the default route's own device, not just any local route --------
export FAKE_DEFAULT_DEV="wlan0"
export FAKE_LAN_SUBNET="192.168.128.0/23"
result=$(tablet_display_lan_subnet)
[[ $result == "192.168.128.0/23" ]] || fail "expected 192.168.128.0/23, got '$result'"

# -- no default route (disconnected host): fails cleanly, no crash ---------
unset FAKE_DEFAULT_DEV
unset FAKE_LAN_SUBNET
if tablet_display_lan_subnet >/dev/null 2>&1; then
  fail "expected failure with no default route, got success"
fi

echo "PASS: tablet_display_lan_subnet"
