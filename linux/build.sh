#!/bin/bash
# Build the Linux packages for one architecture: a .deb, an .rpm and a tarball, all of
# the same /opt/memoreei tree.
#
#   linux/build.sh x86_64|aarch64 [--wheel dist/memoreei-X.whl]
#
# Runs on Linux with uv and nfpm. Either architecture builds on either machine: nothing
# from the tree is executed during the build, so an x86_64 runner can assemble aarch64.
#
# The tree (/opt/memoreei, or ~/.local/opt/memoreei from the tarball):
#   bin/memoreei       the command (linux/memoreei.sh); /usr/bin/memoreei links to it
#   python/            python-build-standalone CPython, with memoreei and
#                      linux/requirements.lock in its own site-packages, via uv --target
#   models/            the fastembed model, so search works offline
#   share/             the user unit, the launcher and its icons, for install.sh to copy
#
# The same recipe as macos/build.sh, not the same code: a shared script would put a
# working Mac build at the mercy of every Linux change.
set -euo pipefail

ARCH=${1:-}
shift || true
WHEEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wheel) WHEEL=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$ARCH" in
  x86_64)  DEB_ARCH=amd64; PBS_SHA=269b2c99e4db15b242bf01832f4fea1e8f1a664f273cff519393f296e9820b41 ;;
  aarch64) DEB_ARCH=arm64; PBS_SHA=c8499b61252c433280f134df954464d19811527b31cb920c35fc6967c1222e35 ;;
  *) echo "usage: $0 x86_64|aarch64 [--wheel PATH]" >&2; exit 2 ;;
esac

PYTHON_VERSION=3.12.14
PBS_RELEASE=20260924
PY_MINOR=${PYTHON_VERSION%.*}
PBS_FILE="cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${ARCH}-unknown-linux-gnu-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE/+/%2B}"
# glibc 2.28: Ubuntu 20.04, Debian 11, RHEL 8. linux/lock.sh resolves for the same floor.
PLATFORM="${ARCH}-manylinux_2_28"

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UV=${UV:-uv}
NFPM=${NFPM:-nfpm}
OUT="$ROOT/linux/build/$ARCH"
CACHE="$ROOT/linux/build/cache"
TREE="$OUT/memoreei"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")
# PEP 440 to semver, which nfpm turns into 0.4.0~rc1 so a final sorts after its rcs.
SEMVER=$(echo "$VERSION" | sed -E 's/^([0-9.]+)(a|b|rc)([0-9]+)$/\1-\2\3/')

step() { printf '\n==> %s\n' "$*"; }

rm -rf "$OUT"
mkdir -p "$OUT" "$CACHE" "$TREE/bin" "$TREE/share/icons"

step "Python $PYTHON_VERSION ($ARCH)"
if [ ! -f "$CACHE/$PBS_FILE" ]; then
  curl -sSLf -o "$CACHE/$PBS_FILE.part" "$PBS_URL"
  mv "$CACHE/$PBS_FILE.part" "$CACHE/$PBS_FILE"
fi
echo "$PBS_SHA  $CACHE/$PBS_FILE" | sha256sum -c -
tar xzf "$CACHE/$PBS_FILE" -C "$TREE"
PY="$TREE/python"
STDLIB="$PY/lib/python$PY_MINOR"
# Tk, IDLE, the test suite, pip, headers, libpython (the interpreter is static; the
# shared library is only for embedding) and the other interpreters' names: never used by
# a server. Bytecode goes to the user's cache (see linux/memoreei.sh), so none is shipped.
rm -rf "$STDLIB"/{tkinter,idlelib,turtledemo,test,ensurepip,lib2to3} "$STDLIB/site-packages"/* \
  "$STDLIB"/lib-dynload/_tkinter* "$PY/lib"/{libtcl*,libtk*,tcl*,tk*,itcl*,thread*,pkgconfig} \
  "$PY/include" "$PY/share" "$PY"/lib/libpython*
find "$PY/bin" -mindepth 1 ! -name "python$PY_MINOR" -delete
find "$PY" -name __pycache__ -prune -exec rm -rf {} +
"$PY/bin/python$PY_MINOR" -c 'import sys' 2>/dev/null \
  && echo "the bundled Python runs here" || echo "the bundled Python is for another architecture"

step "memoreei $VERSION and its locked dependencies"
if [ -z "$WHEEL" ]; then
  "$UV" build -q --wheel -o "$OUT/wheel" "$ROOT"
  WHEEL=$(ls "$OUT"/wheel/memoreei-*.whl)
fi
install=("$UV" pip install -q --target "$STDLIB/site-packages" --python-version "$PY_MINOR"
         --python-platform "$PLATFORM" --only-binary :all:)
"${install[@]}" -r "$ROOT/linux/requirements.lock"
"${install[@]}" --no-deps "$WHEEL"
rm -rf "$STDLIB/site-packages/bin"  # console scripts with a build-machine shebang
find "$STDLIB/site-packages" -name __pycache__ -prune -exec rm -rf {} +

step "Embedding model"
# Downloaded with the build machine's own Python: fastembed lays out its cache the same
# on every platform, and the tree's Python may not run here.
FASTEMBED=$(grep '^fastembed==' "$ROOT/linux/requirements.lock")
MODEL=$(sed -n 's/^ *MODEL_NAME = "\(BAAI[^"]*\)".*/\1/p' "$ROOT/src/memoreei/search/embeddings.py")
FASTEMBED_CACHE_PATH="$TREE/models" "$UV" run -q --no-project --python 3.12 --with "$FASTEMBED" \
  python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='$MODEL')"
rm -rf "$TREE/models"/*.lock "$TREE/models"/.locks "$TREE/models"/tmp

step "Command, service and launcher"
install -m 755 "$ROOT/linux/memoreei.sh" "$TREE/bin/memoreei"
install -m 644 "$ROOT/linux/memoreei.service" "$ROOT/linux/cafe.caleb.Memoreei.desktop" "$TREE/share/"
install -m 644 "$ROOT"/linux/icons/*.png "$TREE/share/icons/"
# Readable by everyone, writable by no one but the owner: it's a program, not data.
chmod -R u+rwX,go+rX,go-w "$TREE"
du -sh "$TREE" | awk '{print "tree: "$1}'

step "Packages"
cd "$ROOT"
NAME="memoreei-$VERSION-linux-$ARCH"
sed "s|@TREE@|$TREE|g" linux/nfpm.yaml >"$OUT/nfpm.yaml"
# nfpm.yaml's ${ARCH}: Debian's name for the .deb; the .rpm's happens to be ours.
export SEMVER ARCH
ARCH=$DEB_ARCH "$NFPM" package -f "$OUT/nfpm.yaml" -p deb -t "$OUT/memoreei_${VERSION}_${DEB_ARCH}.deb"
"$NFPM" package -f "$OUT/nfpm.yaml" -p rpm -t "$OUT/memoreei-${VERSION}.${ARCH}.rpm"
mkdir -p "$OUT/tarball"
cp -a "$TREE" "$OUT/tarball/$NAME"
install -m 755 "$ROOT/linux/install.sh" "$OUT/tarball/$NAME/install.sh"
tar -C "$OUT/tarball" --owner=0 --group=0 -czf "$OUT/$NAME.tar.gz" "$NAME"
rm -rf "$OUT/tarball"
ls -lh "$OUT"/*.deb "$OUT"/*.rpm "$OUT"/*.tar.gz | awk '{print $5"\t"$NF}'
