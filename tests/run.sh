#!/bin/bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_tmp=$(mktemp -d)
trap 'rm -rf -- "$test_tmp"' EXIT

export TEST_TMP_DIR="$test_tmp"
export PATH="$repo_dir/tests/fixtures/bin:$PATH"

cd "$repo_dir"
python3 -m unittest discover -s tests -p 'test_*.py' -v
