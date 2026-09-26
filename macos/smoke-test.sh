#!/bin/bash
# Prove a built Memoreei.app's server works: import, serve, key, search, with no network
# for the model. Runs the bundled server the way the app does, without the menu-bar app
# (CI has no one to click through onboarding).
#
#   macos/smoke-test.sh path/to/Memoreei.app
set -euo pipefail

APP=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SERVER="$APP/Contents/Helpers/Memoreei Server.app/Contents/MacOS/Memoreei Server"
RES="$APP/Contents/Resources"
WORK=$(mktemp -d)
PORT=${SMOKE_PORT:-3697}
trap 'kill "${PID:-0}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

export PYTHONPATH="$RES/site-packages" PYTHONNOUSERSITE=1 PYTHONPYCACHEPREFIX="$WORK/pycache"
export FASTEMBED_CACHE_PATH="$RES/models" HF_HUB_OFFLINE=1 MEMOREEI_HOME="$WORK/home"
# Anything that tries the network fails fast: the model has to come from the bundle.
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9
export NO_PROXY=127.0.0.1,localhost
cd "$WORK"

mem() { "$SERVER" -m memoreei "$@"; }

echo "--- import"
mem import whatsapp "$ROOT/data/samples/whatsapp_printer_conspiracy.txt" >/dev/null
KEY=$(mem key create smoke | grep -o 'mem_[A-Za-z0-9_-]*' | head -1)
[ -n "$KEY" ] || { echo "no key created"; exit 1; }

echo "--- serve on $PORT"
"$SERVER" -m memoreei serve --http --host 127.0.0.1 --port "$PORT" >"$WORK/serve.log" 2>&1 &
PID=$!
for _ in $(seq 1 120); do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/mcp" && break
  sleep 0.5
done

call() {
  curl -sf --noproxy '*' "http://127.0.0.1:$PORT/mcp" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$1"
}

echo "--- no key is refused"
code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/mcp")
[ "$code" = 401 ] || { echo "expected 401, got $code"; exit 1; }

echo "--- tools/list"
call '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | grep -q '"search_memory"' || { cat "$WORK/serve.log"; exit 1; }

echo "--- search"
result=$(call '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_memory","arguments":{"query":"the printer is plotting against us","limit":3}}}')
echo "$result" | grep -q 'whatsapp_printer_conspiracy' || { echo "$result"; cat "$WORK/serve.log"; exit 1; }

echo "--- dashboard answers on loopback"
code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/admin/")
[ "$code" = 401 ] || { echo "expected 401 (signed out), got $code"; exit 1; }

echo "--- signature untouched by running"
codesign --verify --strict "$APP"

echo "smoke test passed: $(file -b "$SERVER" | awk '{print $NF}')"
