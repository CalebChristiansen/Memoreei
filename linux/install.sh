#!/bin/sh
# Install Memoreei from this tarball, for you alone and without root:
#
#   ~/.local/opt/memoreei                              the program (this folder)
#   ~/.local/bin/memoreei                              the command
#   ~/.config/systemd/user/memoreei.service            the service, disabled until opened
#   ~/.local/share/applications/cafe.caleb.Memoreei.desktop, and its icons
#
#   ./install.sh               install, or upgrade (a running Memoreei restarts on it)
#   ./install.sh --uninstall   remove all of the above; your data stays where it is
set -eu
src=$(dirname "$(readlink -f "$0")")
dest=${MEMOREEI_PREFIX:-$HOME/.local/opt/memoreei}
data=${XDG_DATA_HOME:-$HOME/.local/share}
unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
bin=$HOME/.local/bin
desktop=$data/applications/cafe.caleb.Memoreei.desktop
icons=$data/icons/hicolor

if [ "$(id -u)" = 0 ]; then
  echo "Run this as the person whose messages Memoreei will read, not as root." >&2
  echo "For a system-wide install, use the .deb or .rpm instead." >&2
  exit 1
fi
have_systemd() { command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; }

if [ "${1:-}" = --uninstall ]; then
  if have_systemd; then systemctl --user disable --now memoreei.service 2>/dev/null || true; fi
  rm -rf "$dest" "$unit_dir/memoreei.service" "$desktop"
  rm -f "$icons"/*/apps/cafe.caleb.Memoreei.png
  [ "$(readlink "$bin/memoreei" 2>/dev/null)" = "$dest/bin/memoreei" ] && rm -f "$bin/memoreei"
  if have_systemd; then systemctl --user daemon-reload; fi
  echo "Memoreei removed. Your data is still in ${MEMOREEI_HOME:-$data/memoreei}."
  exit 0
fi

# Copied beside the old tree and swapped in, so a running server never sees half of each.
mkdir -p "$(dirname "$dest")" "$unit_dir" "$bin" "$data/applications"
rm -rf "$dest.new" "$dest.old"
cp -a "$src" "$dest.new"
rm -f "$dest.new/install.sh"
[ -d "$dest" ] && mv "$dest" "$dest.old"
mv "$dest.new" "$dest"
rm -rf "$dest.old"

sed "s|/opt/memoreei|$dest|g" "$dest/share/memoreei.service" >"$unit_dir/memoreei.service"
sed "s|^Exec=/usr/bin/memoreei|Exec=$dest/bin/memoreei|" \
  "$dest/share/cafe.caleb.Memoreei.desktop" >"$desktop"
for png in "$dest"/share/icons/*.png; do
  size=$(basename "$png" .png)
  mkdir -p "$icons/${size}x${size}/apps"
  cp "$png" "$icons/${size}x${size}/apps/cafe.caleb.Memoreei.png"
done
ln -sfn "$dest/bin/memoreei" "$bin/memoreei"

if have_systemd; then
  systemctl --user daemon-reload
  systemctl --user try-restart memoreei.service
fi
echo "Memoreei $("$dest/bin/memoreei" --version | cut -d' ' -f2) is installed."
if [ -f /usr/lib/systemd/user/memoreei.service ]; then
  echo "(The system package is installed too; for you, this copy replaces it.)"
fi
case ":$PATH:" in *":$bin:"*) ;; *) echo "Add $bin to your PATH to use the memoreei command." ;; esac
echo "Open it from your applications, or run: memoreei open"
