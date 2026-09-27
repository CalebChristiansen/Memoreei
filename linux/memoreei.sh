#!/bin/sh
# The memoreei command: the bundled Python, kept apart from whatever the system has.
#
# /usr/bin/memoreei (or ~/.local/bin/memoreei, from the tarball) links here. The tree is
# found from this script's real location, so it runs wherever it was unpacked.
#
# Nothing may write into the tree at runtime (it belongs to root, or to a package
# manager that checks it), so bytecode is compiled into ~/.cache/memoreei/pycache on
# first run rather than shipped.
set -e
root=$(dirname "$(dirname "$(readlink -f "$0")")")
cache=${XDG_CACHE_HOME:-$HOME/.cache}/memoreei

# The variables needed before config.env can be found (MEMOREEI_HOME): the user unit
# reads this file too, so the service and this command agree on where the data is.
# The real environment still wins.
envfile=${XDG_CONFIG_HOME:-$HOME/.config}/memoreei/env
if [ -r "$envfile" ]; then
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case $key in '' | \#* | *[!A-Za-z0-9_]*) continue ;; esac
    eval "isset=\${$key+x}"
    value=${value#\"}; value=${value%\"}
    [ -n "$isset" ] || export "$key=$value"
  done <"$envfile"
fi

# The bundled OpenSSL looks where Debian keeps CA certificates. Point it at the right
# bundle elsewhere (Fedora, RHEL, SUSE), unless something already has.
if [ -z "${SSL_CERT_FILE:-}" ]; then
  for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt \
           /etc/ssl/ca-bundle.pem /etc/ssl/cert.pem; do
    if [ -r "$f" ]; then export SSL_CERT_FILE="$f"; break; fi
  done
fi

# The model ships in the tree, so search works offline from the first run.
export FASTEMBED_CACHE_PATH="$root/models" HF_HUB_OFFLINE=1 MEMOREEI_BUNDLE="$root"
exec "$root/python/bin/python3.12" -I -u -X utf8 -X pycache_prefix="$cache/pycache" \
  -m memoreei "$@"
