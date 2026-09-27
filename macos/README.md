# Memoreei.app

The macOS app: a Swift menu-bar app that runs `memoreei serve --http` as a child, with
its own Python and every dependency inside the bundle. Users never see a Terminal.

```bash
macos/build.sh x86_64|arm64 [--dmg]      # on a Mac with Xcode's tools and uv
macos/smoke-test.sh macos/build/arm64/Memoreei.app
macos/lock.sh [--check]                  # re-pin, or prove the pins still fit
swift macos/make-icon.swift && iconutil -c icns macos/build/AppIcon.iconset -o macos/AppIcon.icns
swift macos/make-dmg-background.swift && tiffutil -cathidpicheck \
  macos/build/dmg-bg.png macos/build/dmg-bg@2x.png -out macos/dmg-background.tiff
```

`make-icon.swift` draws the mark (`assets/icon.svg`) at every size: the `.icns`, the Linux
icons and the dashboard's PNG, so they can't drift apart. The menu-bar icon is drawn in
`Memoreei/Brand.swift`, since macOS 12 can't load an SVG.

The DMG window (app, arrow, Applications, a line saying what to do) is laid out by
`dmgbuild` from `dmg-settings.py`, on the background `make-dmg-background.swift` draws.

CI (`.github/workflows/macos.yml`) builds both DMGs on every tag and attaches them to
the GitHub Release; run it by hand for test builds. Only arm64 is smoke-tested there.

## How it fits together

| Piece | Why |
|---|---|
| `Contents/MacOS/Memoreei` (Swift, `Memoreei/*.swift`) | The program macOS holds responsible for file access, so Full Disk Access is granted to *Memoreei*, and it covers the Python child. |
| `Contents/Helpers/Memoreei Server.app` | python-build-standalone with `bin/` renamed `MacOS/`. It's an app so the firewall prompt and Activity Monitor say "Memoreei Server", not "python3.12". CPython finds `../lib` from wherever its executable is. |
| `Contents/Resources/site-packages` | memoreei and `requirements.lock`, installed with `uv pip install --target`. |
| `Contents/Resources/models` | The fastembed model, so search works offline. The app sets `HF_HUB_OFFLINE`. |
| LaunchAgent `<bundle id>.plist` | "Start at login". Its program is the Swift executable itself, never a script (a script would become the responsible process). `KeepAlive` on crash only. |
| `/admin` in the Python package | All setup screens. The app only logs you in (`memoreei admin-url`) and opens the browser. |

Nothing writes into the bundle at runtime, or the signature breaks: bytecode goes to
`~/Library/Caches/Memoreei/pycache` (`PYTHONPYCACHEPREFIX`).

## Pins that matter

- **onnxruntime 1.19.2** (`constraints.txt`): the last release with wheels for macOS
  12 on Intel. Newer ones need macOS 13 (1.20–1.23) or 14 and arm64 only (1.24+).
  fastembed accepts it on Python ≤ 3.12, hence Python 3.12.
- **The lock is resolved for x86_64 with `--only-binary`** and checked against arm64, or
  it picks versions with no macOS 12 wheel.
- **sympy is dropped from the bundle**: onnxruntime needs it only for offline model
  tools.

## Signing

Ad-hoc until there's a Developer ID. Set `MEMOREEI_SIGN_IDENTITY` to sign with the
hardened runtime (`entitlements.plist`); notarisation is still to be added. Until
then every build is a new app to macOS: after each update its Full Disk Access entry is
still listed but switched off, and the user switches it back on (the app notices and
reopens its setup window). The firewall's Allow
survives, because the server binary's bytes don't change unless the Python does.

The bundle identifier becomes permanent at signing: changing it later costs every user a
fresh grant.
