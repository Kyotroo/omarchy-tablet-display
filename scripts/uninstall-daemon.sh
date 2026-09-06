#!/bin/bash
# Stops, disables, and removes the kdm-tablet-displayd systemd --user unit,
# and clears the plugin's runtime/state files. Unlike kdm.presets, this
# plugin has no user-authored data worth preserving (presets.json is a
# bundled asset, not user configuration) so uninstall leaves nothing behind.
set -euo pipefail

unit_dir="$HOME/.config/systemd/user"
unit_name="kdm-tablet-displayd.service"

systemctl --user disable --now "$unit_name" 2>/dev/null || true
rm -f -- "$unit_dir/$unit_name"
systemctl --user daemon-reload

runtime_dir=${XDG_RUNTIME_DIR:-/tmp}
rm -f -- "$runtime_dir/kdm-tablet-displayd.sock" "$runtime_dir/kdm-tablet-displayd-wayvnc.sock"

state_home=${XDG_STATE_HOME:-"$HOME/.local/state"}
rm -rf -- "$state_home/omarchy/kdm-tablet-display"

echo "kdm-tablet-displayd stopped and removed."
