#!/bin/bash
# Tests tablet_display_ensure_wayvnc against fake `wayvnc`/`pacman`/
# `pkexec`/`sudo`. Each case builds its own minimal PATH so the real host's
# own wayvnc/pacman/pkexec (this dev machine has all three) can never leak
# into what's under test -- a real pkexec would try to open a real polkit
# prompt.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fixtures_dir="$repo_dir/tests/fixtures/bin"
# shellcheck source=../scripts/lib-privilege.sh
source "$repo_dir/scripts/lib-privilege.sh"
# shellcheck source=../scripts/lib-deps.sh
source "$repo_dir/scripts/lib-deps.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }

# -- tablet_display_sudo prefers pkexec over sudo when both exist -------
# (pkexec is what makes a prompt work at all from a backgrounded,
# terminal-less caller like the panel's "Set up now" button; sudo alone
# fails silently there). Distinguishable fake binaries, not the shared
# passthrough fixtures, so the test can tell which one actually ran.
marker_dir=$(mktemp -d)
cat >"$marker_dir/pkexec" <<'EOF'
#!/bin/bash
echo "ran-via-pkexec"
EOF
cat >"$marker_dir/sudo" <<'EOF'
#!/bin/bash
echo "ran-via-sudo"
EOF
chmod +x "$marker_dir/pkexec" "$marker_dir/sudo"

output=$(PATH="$marker_dir" tablet_display_sudo true)
[[ $output == "ran-via-pkexec" ]] || fail "expected pkexec to be preferred, got: $output"

rm -f -- "$marker_dir/pkexec"
output=$(PATH="$marker_dir" tablet_display_sudo true)
[[ $output == "ran-via-sudo" ]] || fail "expected fallback to sudo when pkexec is absent, got: $output"
rm -rf -- "$marker_dir"

echo "PASS: tablet_display_sudo"

# -- already installed: no pacman/pkexec call, no output ----------------
already_dir=$(mktemp -d)
cp -- "$fixtures_dir/wayvnc" "$already_dir/wayvnc"
output=$(PATH="$already_dir" tablet_display_ensure_wayvnc 2>&1)
[[ -z $output ]] || fail "expected no output when wayvnc is already present, got: $output"
rm -rf -- "$already_dir"

# -- missing, install succeeds (via the fake pkexec, not sudo) ----------
minimal_dir=$(mktemp -d)
cp -- "$fixtures_dir/pacman" "$fixtures_dir/pkexec" "$minimal_dir/"
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
