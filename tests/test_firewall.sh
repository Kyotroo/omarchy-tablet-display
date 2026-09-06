#!/bin/bash
# Tests tablet_display_lan_subnet against a fake `ip` (see fixtures/bin/ip),
# and tablet_display_firewall_allow's happy/failure paths against fake
# `ufw`/`pkexec`. Does not exercise tablet_display_firewall_remove's actual
# `ufw` calls -- its stateful delete loop touches real system firewall
# state and is verified manually/live instead.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fixtures_dir="$repo_dir/tests/fixtures/bin"
# shellcheck source=../scripts/lib-privilege.sh
source "$repo_dir/scripts/lib-privilege.sh"
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

# -- firewall_allow: succeeds via pkexec (not a bare, TTY-less sudo) -------
export FAKE_DEFAULT_DEV="wlan0"
export FAKE_LAN_SUBNET="192.168.128.0/23"
minimal_dir=$(mktemp -d)
cp -- "$fixtures_dir/ip" "$fixtures_dir/ufw" "$fixtures_dir/pkexec" "$minimal_dir/"
unset FAKE_UFW_FAILS
output=$(PATH="$minimal_dir:$PATH" tablet_display_firewall_allow 2>&1)
grep -q "allowed TCP" <<<"$output" || fail "expected allow success message, got: $output"

# -- firewall_allow: prompt dismissed/failed -> prints manual fallback ----
export FAKE_UFW_FAILS=1
output=$(PATH="$minimal_dir:$PATH" tablet_display_firewall_allow 2>&1) || true
grep -q "sudo ufw allow from 192.168.128.0/23" <<<"$output" \
  || fail "expected manual fallback command, got: $output"
unset FAKE_UFW_FAILS FAKE_DEFAULT_DEV FAKE_LAN_SUBNET
rm -rf -- "$minimal_dir"

echo "PASS: tablet_display_firewall_allow"
