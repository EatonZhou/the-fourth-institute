#!/usr/bin/env bash
set -euo pipefail
# Supply an isolated upstream checkout; never use the Portal worktree here.
source_dir=${1:?usage: bash build.sh /path/to/reader-checkout}
prototype_dir=$(cd -- "$(dirname -- "$0")" && pwd)
test "$(git -C "$source_dir" rev-parse HEAD)" = 04795bfbafd55600e042d62e05f9a4147458ae53
git -C "$source_dir" diff --quiet
git -C "$source_dir" submodule update --init --recursive
git -C "$source_dir" apply --check "$prototype_dir/save-contract.patch"
git -C "$source_dir" apply "$prototype_dir/save-contract.patch"
cd -- "$source_dir"
npm ci --ignore-scripts --no-audit --no-fund
PDFJS_CONFIG=web bash pdfjs/build
npx webpack --config-name web
rsync -aH -- build/web/ "$prototype_dir/upstream/"
rsync -aH -- COPYING "$prototype_dir/upstream/COPYING"
