#!/bin/bash
# Tests tablet_display_ensure_wayvnc against fake `wayvnc`/`pacman`/`sudo`.
# Each case builds its own minimal PATH so the real host's own wayvnc/pacman
# (this dev machine has both) can never leak into what's under test.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fixtures_dir="$repo_dir/tests/fixtures/bin"
# shellcheck source=../scripts/lib-deps.sh
source "$repo_dir/scripts/lib-deps.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }

# -- already installed: no pacman/sudo call, no output -----------------
already_dir=$(mktemp -d)
cp -- "$fixtures_dir/wayvnc" "$already_dir/wayvnc"
output=$(PATH="$already_dir" tablet_display_ensure_wayvnc 2>&1)
[[ -z $output ]] || fail "expected no output when wayvnc is already present, got: $output"
rm -rf -- "$already_dir"

# -- missing, install succeeds -------------------------------------------
minimal_dir=$(mktemp -d)
cp -- "$fixtures_dir/pacman" "$fixtures_dir/sudo" "$minimal_dir/"
unset FAKE_PACMAN_FAILS
output=$(PATH="$minimal_dir" tablet_display_ensure_wayvnc 2>&1)
grep -q "installed wayvnc" <<<"$output" || fail "expected success message, got: $output"

# -- missing, install fails: prints the manual fallback command --------
export FAKE_PACMAN_FAILS=1
output=$(PATH="$minimal_dir" tablet_display_ensure_wayvnc 2>&1) || true
grep -q "sudo pacman -S wayvnc" <<<"$output" || fail "expected manual fallback command, got: $output"
unset FAKE_PACMAN_FAILS
rm -rf -- "$minimal_dir"

echo "PASS: tablet_display_ensure_wayvnc"
