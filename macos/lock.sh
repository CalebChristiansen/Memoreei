#!/bin/bash
# Regenerate macos/requirements.lock: every dependency of memoreei, pinned, for the app.
#
#   macos/lock.sh           re-resolve from scratch (picks up new releases)
#   macos/lock.sh --check   only prove the lock still satisfies pyproject.toml, on both
#                           architectures (what CI runs; it never changes a pin)
#
# One lock serves both architectures. It's resolved for x86_64 (the stricter of the two:
# some packages have dropped Intel wheels for macOS 11) and then checked against arm64.
# --only-binary matters: without it the resolver picks versions that exist on PyPI but
# have no wheel for macOS 11, and the build fails instead.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-uv}
export MACOSX_DEPLOYMENT_TARGET=11.0
common=(--only-binary :all: --python-version 3.12 --no-header --no-annotate -q)

if [ "${1:-}" != --check ]; then
  "$UV" pip compile pyproject.toml -c macos/constraints.txt "${common[@]}" \
    --python-platform x86_64-apple-darwin -o macos/requirements.lock
fi

check=$(mktemp)
trap 'rm -f "$check"' EXIT
for platform in x86_64-apple-darwin aarch64-apple-darwin; do
  "$UV" pip compile pyproject.toml -c macos/constraints.txt -c macos/requirements.lock \
    "${common[@]}" --python-platform "$platform" -o "$check"
  if ! diff -q macos/requirements.lock "$check" >/dev/null; then
    echo "$platform resolves differently from macos/requirements.lock; run macos/lock.sh:" >&2
    diff macos/requirements.lock "$check" >&2
    exit 1
  fi
done
echo "macos/requirements.lock: $(grep -c '==' macos/requirements.lock) packages, both architectures"
