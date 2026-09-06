#!/bin/bash
# Installs and starts the kdm-tablet-displayd systemd --user unit.
# Idempotent: safe to run again (e.g. on plugin update).
set -euo pipefail

plugin_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
unit_dir="$HOME/.config/systemd/user"
unit_name="kdm-tablet-displayd.service"

# shellcheck source=lib-privilege.sh
source "$plugin_dir/scripts/lib-privilege.sh"
# shellcheck source=lib-deps.sh
source "$plugin_dir/scripts/lib-deps.sh"
tablet_display_ensure_wayvnc

mkdir -p -- "$unit_dir"
cp -- "$plugin_dir/systemd/$unit_name" "$unit_dir/$unit_name"

systemctl --user daemon-reload
systemctl --user enable --now "$unit_name"

echo "kdm-tablet-displayd installed and started."

# shellcheck source=lib-firewall.sh
source "$plugin_dir/scripts/lib-firewall.sh"
tablet_display_firewall_allow
