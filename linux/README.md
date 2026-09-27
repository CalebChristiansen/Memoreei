# Memoreei for Linux

A `.deb`, an `.rpm` and a tarball, all of one tree: its own Python, every dependency and
the search model, run as a systemd user unit by the person whose messages it reads.

```bash
linux/build.sh x86_64|aarch64            # on Linux with uv and nfpm; either arch on either machine
sudo linux/smoke-test.sh linux/build/x86_64/memoreei_X.Y.Z_amd64.deb   # or the .rpm, .tar.gz
linux/lock.sh [--check]                  # re-pin, or prove the pins still fit
```

Output lands in `linux/build/<arch>/`: `memoreei_X.Y.Z_{amd64,arm64}.deb`,
`memoreei-X.Y.Z.{x86_64,aarch64}.rpm` and `memoreei-X.Y.Z-linux-{x86_64,aarch64}.tar.gz`.

CI (`.github/workflows/linux.yml`) builds all six on every tag and smoke-tests each in
containers: Ubuntu 20.04 and 24.04, Debian 12, Fedora and Rocky 8, on both
architectures. Run it by hand for test builds.

## How it fits together

| Piece | Why |
|---|---|
| `/opt/memoreei/bin/memoreei` (`memoreei.sh`) | The command. `/usr/bin/memoreei` links to it. Runs the bundled Python isolated (`-I`), so nothing from the system's Python leaks in, and finds the tree from its own path, so the tarball runs from `~/.local/opt`. |
| `/opt/memoreei/python` | python-build-standalone, glibc build, with memoreei and `requirements.lock` in its own `site-packages` (`uv pip install --target`). Tk, the test suite, pip and libpython are dropped: the interpreter is static. |
| `/opt/memoreei/models` | The fastembed model, so search works offline. The command sets `HF_HUB_OFFLINE`. |
| `/usr/lib/systemd/user/memoreei.service` | Shipped **disabled**. `memoreei open` (the launcher) or `memoreei service install` enables it. Exit 75 means the port is held, and stops the restarts. |
| `~/.config/memoreei/env` | The few variables needed before `config.env` can be found, `MEMOREEI_HOME` above all. The unit and the command both read it, so they agree on where the data is. |
| `cafe.caleb.Memoreei.desktop` | Runs `memoreei open`: start the unit if needed, wait for the port, open a one-time dashboard link. Problems (port held by another user, a crash at start) come up as a dialog, since there's no terminal to read. |
| `postinst` / `prerm` / `postrm` | `daemon-reload` and `try-restart` in every running user manager, lingering ones included, so an upgrade restarts a running server on the new build. Through `runuser`, because `systemctl --user -M user@` needs systemd 248 and RHEL 8 has 239. Removal stops and disables it; data stays. |
| `install.sh` (tarball only) | The same files under `~/.local`, no root. The tree is copied beside the old one and swapped in, so an upgrade never runs half of each. |

Nothing writes into the tree at runtime: bytecode goes to `~/.cache/memoreei/pycache`
(`-X pycache_prefix`), and the smoke test proves it with `dpkg --verify` / `rpm -V`.
The bundled OpenSSL looks for CA certificates where Debian keeps them, so the command
points `SSL_CERT_FILE` at Fedora's, RHEL's or SUSE's bundle when it's elsewhere.

A packaged install ignores a `.env` in the current directory, which pip installs read for
development: the unit runs in the home folder, where a `.env` is more likely some other
project's.

## Pins that matter

- **manylinux_2_28** (glibc 2.28): the floor for every wheel in the lock, and so for the
  packages. Ubuntu 20.04, Debian 11, RHEL 8.
- **Its own lock**, on current onnxruntime. The Mac's `constraints.txt` holds it at 1.19.2
  for macOS 12; nothing holds Linux back that way.
- **xz**, not zstd, inside the .deb and .rpm: Debian 11's dpkg can't read zstd.

## Upgrades and data

Updates are by hand: the dashboard checks GitHub's latest release once a day
(`memoreei/updates.py`, packaged installs only) and says when there's a newer one. Data
lives in `~/.local/share/memoreei` (`$XDG_DATA_HOME`). Versions before 0.4 used
`~/.memoreei` on Linux; nothing migrates it, and the dashboard says where it is.
