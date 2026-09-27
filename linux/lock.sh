#!/bin/bash
# Regenerate linux/requirements.lock: every dependency of memoreei, pinned, for the
# Linux packages.
#
#   linux/lock.sh           re-resolve from scratch (picks up new releases)
#   linux/lock.sh --check   only prove the lock still satisfies pyproject.toml, on both
#                           architectures (what CI runs; it never changes a pin)
#
# One lock serves both architectures: resolved for x86_64 and then checked against
# aarch64. The floor is manylinux_2_28 (glibc 2.28: Ubuntu 20.04, Debian 11, RHEL 8), and
# --only-binary keeps the resolver to versions with a wheel for it. This lock follows
# current onnxruntime; it isn't held to macos/requirements.lock's macOS 12 pins.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-uv}
common=(--only-binary :all: --python-version 3.12 --no-header --no-annotate -q)

if [ "${1:-}" != --check ]; then
  "$UV" pip compile pyproject.toml "${common[@]}" \
    --python-platform x86_64-manylinux_2_28 -o linux/requirements.lock
fi

check=$(mktemp)
trap 'rm -f "$check"' EXIT
for platform in x86_64-manylinux_2_28 aarch64-manylinux_2_28; do
  "$UV" pip compile pyproject.toml -c linux/requirements.lock \
    "${common[@]}" --python-platform "$platform" -o "$check"
  if ! diff -q linux/requirements.lock "$check" >/dev/null; then
    echo "$platform resolves differently from linux/requirements.lock; run linux/lock.sh:" >&2
    diff linux/requirements.lock "$check" >&2
    exit 1
  fi
done
echo "linux/requirements.lock: $(grep -c '==' linux/requirements.lock) packages, both architectures"
