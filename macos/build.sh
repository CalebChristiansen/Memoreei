#!/bin/bash
# Build Memoreei.app (and optionally its DMG) for one architecture.
#
#   macos/build.sh x86_64|arm64 [--wheel dist/memoreei-X.whl] [--dmg]
#
# Runs on macOS with Xcode's command-line tools and uv. Either architecture builds on
# either kind of Mac: nothing from the bundle is executed during the build, so an arm64
# runner can assemble the x86_64 app.
#
# The bundle:
#   Contents/MacOS/Memoreei                  the Swift menu-bar app (macos/Memoreei/*.swift)
#   Contents/Helpers/Memoreei Server.app     python-build-standalone CPython, as an app:
#       Contents/MacOS/Memoreei Server       its bin/python3.12, renamed
#       Contents/lib/                        its lib/ (libpython and the standard library)
#   Contents/Resources/site-packages         memoreei + macos/requirements.lock, via uv --target
#   Contents/Resources/models/               the fastembed model, so search works offline
#
# Python is wrapped in an app of its own so that macOS names it: the firewall asks about
# whichever program listens on the port, and "Memoreei Server" means something to a
# person where "python3.12" doesn't. CPython finds its lib/ relative to the executable
# (../lib), so renaming bin/ to MacOS/ keeps it working unchanged.
#
# Nothing may write into the bundle at runtime (it would break the code signature), so
# the app points PYTHONPYCACHEPREFIX at ~/Library/Caches/Memoreei: bytecode is compiled
# there on first run rather than shipped (it would add 130 MB).
set -euo pipefail

ARCH=${1:-}
shift || true
WHEEL=""
MAKE_DMG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --wheel) WHEEL=$2; shift 2 ;;
    --dmg) MAKE_DMG=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$ARCH" in
  x86_64) PBS_ARCH=x86_64;  PBS_SHA=7ea9761b9069c10b9a20531d568645849d604c59e9c7f11f6659f1e1790c968e ;;
  arm64)  PBS_ARCH=aarch64; PBS_SHA=c2edb321cd32ec2b170df208db0446dccc4398db602ca27cf2079098fb1f7d9d ;;
  *) echo "usage: $0 x86_64|arm64 [--wheel PATH] [--dmg]" >&2; exit 2 ;;
esac

# The bundle identifier becomes permanent the day the app is signed with a Developer ID:
# changing it after that costs every user a fresh Full Disk Access grant.
BUNDLE_ID=cafe.caleb.Memoreei
MIN_MACOS=12.0
PYTHON_VERSION=3.12.14
PBS_RELEASE=20260924
PY_MINOR=${PYTHON_VERSION%.*}
PBS_FILE="cpython-${PYTHON_VERSION}+${PBS_RELEASE}-${PBS_ARCH}-apple-darwin-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE/+/%2B}"
# Ad-hoc ("-") until there's a Developer ID; set it to the identity's name to sign.
SIGN_IDENTITY=${MEMOREEI_SIGN_IDENTITY:--}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
UV=${UV:-uv}
OUT="$ROOT/macos/build/$ARCH"
CACHE="$ROOT/macos/build/cache"
APP="$OUT/Memoreei.app"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")
PLATFORM="${PBS_ARCH}-apple-darwin"
export MACOSX_DEPLOYMENT_TARGET=$MIN_MACOS

step() { printf '\n==> %s\n' "$*"; }

rm -rf "$OUT"
mkdir -p "$OUT" "$CACHE" "$APP/Contents/MacOS" "$APP/Contents/Resources"
RES="$APP/Contents/Resources"

step "Python $PYTHON_VERSION ($PBS_ARCH)"
if [ ! -f "$CACHE/$PBS_FILE" ]; then
  curl -sSLf -o "$CACHE/$PBS_FILE.part" "$PBS_URL"
  mv "$CACHE/$PBS_FILE.part" "$CACHE/$PBS_FILE"
fi
echo "$PBS_SHA  $CACHE/$PBS_FILE" | shasum -a 256 -c -
tar xzf "$CACHE/$PBS_FILE" -C "$OUT"
SERVER="$APP/Contents/Helpers/Memoreei Server.app"
mkdir -p "$SERVER/Contents/MacOS"
mv "$OUT/python/bin/python$PY_MINOR" "$SERVER/Contents/MacOS/Memoreei Server"
mv "$OUT/python/lib" "$SERVER/Contents/lib"
rm -rf "$OUT/python"
# Tk, IDLE, the test suite and pip: never used by a server, and a third of the size.
STDLIB="$SERVER/Contents/lib/python$PY_MINOR"
rm -rf "$STDLIB"/{tkinter,idlelib,turtledemo,test,ensurepip,lib2to3} "$STDLIB/site-packages"/* \
  "$STDLIB"/lib-dynload/_tkinter* "$SERVER/Contents/lib"/{libtcl*,libtk*,tcl*,tk*,itcl*,thread*,pkgconfig}
"$SERVER/Contents/MacOS/Memoreei Server" -c 'import sys' 2>/dev/null \
  && echo "the bundled Python runs here" || echo "the bundled Python is for another architecture"

step "memoreei $VERSION and its locked dependencies"
if [ -z "$WHEEL" ]; then
  "$UV" build -q --wheel -o "$OUT/wheel" "$ROOT"
  WHEEL=$(ls "$OUT"/wheel/memoreei-*.whl)
fi
install=("$UV" pip install -q --target "$RES/site-packages" --python-version 3.12
         --python-platform "$PLATFORM" --only-binary :all:)
"${install[@]}" -r "$ROOT/macos/requirements.lock"
"${install[@]}" --no-deps "$WHEEL"
rm -rf "$RES/site-packages/bin"  # console scripts with a build-machine shebang
# onnxruntime requires sympy (and so mpmath) only for its offline model tools
# (onnxruntime/tools/symbolic_shape_infer.py); inference never imports it. 77 MB.
rm -rf "$RES/site-packages"/{sympy,mpmath}{,-*.dist-info}

step "Embedding model"
# Downloaded with the build machine's own Python: fastembed lays out its cache the same
# on every platform, and the bundle's Python may not run here.
FASTEMBED=$(grep '^fastembed==' "$ROOT/macos/requirements.lock")
MODEL=$(sed -n 's/^ *MODEL_NAME = "\(BAAI[^"]*\)".*/\1/p' "$ROOT/src/memoreei/search/embeddings.py")
FASTEMBED_CACHE_PATH="$RES/models" "$UV" run -q --no-project --python 3.12 --with "$FASTEMBED" \
  python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='$MODEL')"
rm -rf "$RES/models"/*.lock "$RES/models"/.locks "$RES/models"/tmp

step "Menu-bar app"
xcrun swiftc -O -target "${ARCH}-apple-macos${MIN_MACOS}" \
  -o "$APP/Contents/MacOS/Memoreei" "$ROOT"/macos/Memoreei/*.swift
SHORT_VERSION=${VERSION%%[a-z]*}  # CFBundleShortVersionString is numbers only
fill() {
  sed -e "s/@BUNDLE_ID@/$BUNDLE_ID/" -e "s/@VERSION@/$VERSION/" \
      -e "s/@SHORT_VERSION@/$SHORT_VERSION/" -e "s/@MIN_MACOS@/$MIN_MACOS/" "$1"
}
fill "$ROOT/macos/Info.plist" > "$APP/Contents/Info.plist"
fill "$ROOT/macos/Server-Info.plist" > "$SERVER/Contents/Info.plist"
mkdir -p "$SERVER/Contents/Resources"
cp "$ROOT/macos/AppIcon.icns" "$RES/AppIcon.icns"
cp "$ROOT/macos/AppIcon.icns" "$SERVER/Contents/Resources/AppIcon.icns"

step "Signing ($([ "$SIGN_IDENTITY" = - ] && echo ad-hoc || echo "$SIGN_IDENTITY"))"
# Inside out: every Mach-O in Resources, then the app. --deep would do it too, but
# Apple says not to rely on it, and notarisation checks each binary.
sign=(codesign --force --timestamp=none -s "$SIGN_IDENTITY")
if [ "$SIGN_IDENTITY" != - ]; then
  sign=(codesign --force --timestamp --options runtime
        --entitlements "$ROOT/macos/entitlements.plist" -s "$SIGN_IDENTITY")
fi
find "$RES" "$SERVER/Contents/lib" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 \
  | while IFS= read -r -d '' f; do
      if file -b "$f" | grep -q Mach-O; then printf '%s\0' "$f"; fi
    done | xargs -0 -n 50 "${sign[@]}" 2>&1 | grep -v 'replacing existing signature' || true
"${sign[@]}" "$SERVER"
"${sign[@]}" "$APP"
codesign --verify --strict "$APP"

du -sh "$APP" | awk '{print "app: "$1}'

if [ "$MAKE_DMG" = 1 ]; then
  step "DMG"
  STAGE="$OUT/dmg"
  mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  DMG="$OUT/Memoreei-$ARCH.dmg"
  hdiutil create -quiet -volname Memoreei -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
  rm -rf "$STAGE"
  if [ "$SIGN_IDENTITY" != - ]; then codesign --force --timestamp -s "$SIGN_IDENTITY" "$DMG"; fi
  du -sh "$DMG" | awk '{print "dmg: "$1}'
fi
