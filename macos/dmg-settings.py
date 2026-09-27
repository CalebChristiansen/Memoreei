# dmgbuild settings for Memoreei-<arch>.dmg: the familiar window, the app on the left,
# an arrow, and Applications on the right to drag it onto. build.sh passes the paths:
#   dmgbuild -s macos/dmg-settings.py -D app=… -D background=… -D icon=… Memoreei out.dmg
#
# hdiutil can't lay a window out: that's a .DS_Store, which only Finder writes. dmgbuild
# writes one itself, so this runs without a screen (CI). The layout and background are
# create-dmg's, captioned by make-dmg-background.swift (see dmg-background/LICENSE).
import os.path

app = defines["app"]  # noqa: F821 (dmgbuild provides `defines`)
name = os.path.basename(app)

format = "UDZO"
filesystem = "HFS+"
files = [app]
symlinks = {"Applications": "/Applications"}
icon = defines["icon"]  # noqa: F821
background = defines["background"]  # noqa: F821

window_rect = ((200, 120), (660, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 160
text_size = 12
icon_locations = {name: (180, 170), "Applications": (480, 170)}
hide_extensions = [name]
