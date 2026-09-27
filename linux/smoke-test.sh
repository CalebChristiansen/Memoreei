#!/bin/bash
# Prove a built Linux package works the way a stranger would use it: install it, run the
# service as a throwaway user, import, key, serve, search, with no network for the
# model; then remove it.
#
#   sudo linux/smoke-test.sh linux/build/x86_64/memoreei_0.4.0_amd64.deb   (or .rpm, .tar.gz)
#
# Runs as root: it installs the package and creates (and afterwards deletes) the user
# $SMOKE_USER, memsmoke by default. Where systemd runs (a real machine, a VM), the
# user's own unit is started through linger, as a headless install would be. In a
# container without systemd, the unit's ExecStart line is run as that user instead:
# the same command, minus the manager.
set -euo pipefail

PKG=$(readlink -f "$1")
ROOT=$(cd "$(dirname "$0")/.." && pwd)
U=${SMOKE_USER:-memsmoke}
PORT=${SMOKE_PORT:-3697}
SAMPLE="$ROOT/data/samples/whatsapp_printer_conspiracy.txt"

[ "$(id -u)" = 0 ] || { echo "run as root (it installs a package and adds a user)" >&2; exit 2; }
if id "$U" >/dev/null 2>&1; then
  echo "user $U already exists; this test makes and deletes its own. SMOKE_USER=… to pick another" >&2
  exit 2
fi

step() { printf '\n--- %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

SYSTEMD=0
if [ -d /run/systemd/system ] && command -v loginctl >/dev/null; then SYSTEMD=1; fi
PID=""
cleanup() {
  set +e
  [ -n "$PID" ] && kill "$PID" 2>/dev/null
  if [ "$SYSTEMD" = 1 ]; then loginctl disable-linger "$U" 2>/dev/null; loginctl terminate-user "$U" 2>/dev/null; fi
  sleep 1
  pkill -u "$U" 2>/dev/null
  userdel -r "$U" 2>/dev/null
}
trap cleanup EXIT

step "install $(basename "$PKG")"
useradd -m -s /bin/sh "$U"
UID_=$(id -u "$U")
H=$(getent passwd "$U" | cut -d: -f6)
as_user() {
  runuser -u "$U" -- env -i -C "$H" HOME="$H" USER="$U" LOGNAME="$U" \
    PATH="$H/.local/bin:/usr/local/bin:/usr/bin:/bin" XDG_RUNTIME_DIR="/run/user/$UID_" "$@"
}
case "$PKG" in
  *.deb)
    if command -v apt-get >/dev/null; then
      DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "$PKG" >/dev/null
    else
      dpkg -i "$PKG"
    fi
    TREE=/opt/memoreei UNIT=/usr/lib/systemd/user/memoreei.service
    verify() { dpkg --verify memoreei; }
    uninstall() { apt-get remove -y -q memoreei >/dev/null 2>&1 || dpkg -r memoreei; } ;;
  *.rpm)
    if command -v dnf >/dev/null; then dnf install -y -q "$PKG"; else rpm -i "$PKG"; fi
    TREE=/opt/memoreei UNIT=/usr/lib/systemd/user/memoreei.service
    verify() { rpm -V memoreei; }
    uninstall() { rpm -e memoreei; } ;;
  *.tar.gz)
    cp "$PKG" "$H/" && chown "$U:" "$H/$(basename "$PKG")"
    as_user tar xzf "$H/$(basename "$PKG")" -C "$H"
    as_user sh -c "$H/memoreei-*-linux-*/install.sh"
    TREE=$H/.local/opt/memoreei UNIT=$H/.config/systemd/user/memoreei.service
    verify() { :; }
    uninstall() { as_user "$H"/memoreei-*-linux-*/install.sh --uninstall; } ;;
  *) fail "not a .deb, .rpm or .tar.gz: $PKG" ;;
esac
command -v memoreei >/dev/null || [ -x "$H/.local/bin/memoreei" ] || fail "no memoreei command"
[ -f "$UNIT" ] || fail "no unit at $UNIT"
as_user memoreei --version

step "the bundled OpenSSL finds this system's CA certificates"
# The wrapper's own lookup, run as it would be.
as_user sh -c "$(sed -n '/SSL_CERT_FILE:-/,/^fi$/p' "$TREE/bin/memoreei")
  exec $TREE/python/bin/python3.12 -I -c 'import ssl, sys
n = ssl.create_default_context().cert_store_stats()[\"x509_ca\"]
print(n, \"CA certificates\"); sys.exit(n == 0)'"

step "no network: the model has to come from the package"
# The env file the unit and the command both read, as a user would set MEMOREEI_HOME.
as_user mkdir -p "$H/.config/memoreei"
as_user tee "$H/.config/memoreei/env" >/dev/null <<EOF
MEMOREEI_PORT=$PORT
HTTP_PROXY=http://127.0.0.1:9
HTTPS_PROXY=http://127.0.0.1:9
ALL_PROXY=http://127.0.0.1:9
NO_PROXY=127.0.0.1,localhost
EOF

step "import and key"
cp "$SAMPLE" "$H/" && chown "$U:" "$H/$(basename "$SAMPLE")"
as_user memoreei import whatsapp "$H/$(basename "$SAMPLE")" >/dev/null
[ -f "$H/.local/share/memoreei/memoreei.db" ] || fail "no database in ~/.local/share/memoreei"
KEY=$(as_user memoreei key create smoke | grep -o 'mem_[A-Za-z0-9_-]*' | head -1)
[ -n "$KEY" ] || fail "no key created"

step "serve, as $U's user unit"
if [ "$SYSTEMD" = 1 ]; then
  loginctl enable-linger "$U"
  for _ in $(seq 1 50); do [ -S "/run/user/$UID_/systemd/private" ] && break; sleep 0.2; done
  as_user memoreei service install </dev/null
  as_user systemctl --user is-enabled memoreei.service
else
  echo "(no systemd here: running the unit's ExecStart)"
  exec_line=$(sed -n 's/^ExecStart=//p' "$UNIT")
  as_user sh -c "exec $exec_line" >"$H/serve.log" 2>&1 &
  PID=$!
fi
for _ in $(seq 1 120); do
  curl -s -o /dev/null --noproxy '*' "http://127.0.0.1:$PORT/mcp" && break
  sleep 0.5
done
show_log() {
  if [ "$SYSTEMD" = 1 ]; then as_user journalctl --user -u memoreei -n 40 --no-pager; else cat "$H/serve.log"; fi
}

call() {
  curl -sf --noproxy '*' "http://127.0.0.1:$PORT/mcp" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$1"
}

step "no key is refused"
code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/mcp")
[ "$code" = 401 ] || { show_log; fail "expected 401, got $code"; }

step "tools/list"
call '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | grep -q '"search_memory"' || { show_log; fail "no search_memory"; }

step "search"
result=$(call '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_memory","arguments":{"query":"the printer is plotting against us","limit":3}}}')
echo "$result" | grep -q 'whatsapp_printer_conspiracy' || { echo "$result"; show_log; fail "search"; }

step "dashboard: signed out, then in with admin-url's one-time link"
code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/admin/")
[ "$code" = 401 ] || fail "expected 401 (signed out), got $code"
out=$(as_user memoreei admin-url 2>&1)
echo "$out" | grep -q "ssh -L $PORT:localhost:$PORT $U@" || { echo "$out"; fail "no ssh -L hint"; }
link=$(echo "$out" | grep -o 'http://localhost[^ ]*token=[A-Za-z0-9_-]*')
jar=$(mktemp)
curl -s --noproxy '*' -c "$jar" -o /dev/null "$link"
code=$(curl -s --noproxy '*' -b "$jar" -o "$jar.html" -w '%{http_code}' "http://localhost:$PORT/admin/")
[ "$code" = 200 ] || fail "signed-in dashboard: $code"
grep -q 'Memories' "$jar.html" || fail "dashboard has no status"
rm -f "$jar" "$jar.html"

step "nothing wrote into the package's files"
verify

if [ "$SYSTEMD" = 1 ]; then
  step "upgrade in place restarts the running service (postinst)"
  before=$(as_user systemctl --user show -p MainPID --value memoreei.service)
  case "$PKG" in
    *.deb) dpkg -i "$PKG" >/dev/null ;;
    *.rpm) rpm -U --replacepkgs "$PKG" ;;
    *.tar.gz) as_user sh -c "$H/memoreei-*-linux-*/install.sh" >/dev/null ;;
  esac
  sleep 1
  after=$(as_user systemctl --user show -p MainPID --value memoreei.service)
  [ "$before" != "$after" ] && [ "$after" != 0 ] || fail "not restarted ($before → $after)"
  echo "restarted: $before → $after"
fi

step "remove"
uninstall
[ ! -e "$TREE" ] || fail "$TREE is still there"
[ -f "$H/.local/share/memoreei/memoreei.db" ] || fail "removing the package took the data"
if [ "$SYSTEMD" = 1 ]; then
  state=$(as_user systemctl --user is-active memoreei.service || true)
  [ "$state" != active ] || fail "still running after removal"
fi

echo
echo "smoke test passed: $(basename "$PKG") on $(. /etc/os-release && echo "$PRETTY_NAME") $(uname -m)"
