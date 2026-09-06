#!/bin/bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_tmp=$(mktemp -d)

# Belt-and-suspenders: fake wayvnc is a long-running process, and a rare
# thread-timing race in WayvncSupervisor.stop() (only ever observed running
# the full suite back-to-back, never a single test in isolation) can let one
# outlive its test's tearDown. Its own tmp dir gets deleted by the trap
# below regardless, which would otherwise orphan it silently. Killing by
# this run's own tmp path is safe (it can only ever match this run's own
# fixtures, never a real wayvnc) and guarantees a clean process table
# whether or not the underlying race gets root-caused.
cleanup() {
  pkill -f "fixtures/bin/wayvnc.*$test_tmp" 2>/dev/null || true
  rm -rf -- "$test_tmp"
}
trap cleanup EXIT

export TEST_TMP_DIR="$test_tmp"
export PATH="$repo_dir/tests/fixtures/bin:$PATH"

cd "$repo_dir"
python3 -m unittest discover -s tests -p 'test_*.py' -v
