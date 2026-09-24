"""Unit tests for LaunchdBackend and SystemdBackend."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import typer
from click.exceptions import Exit as ClickExit

from memoreei.service._launchd import LaunchdBackend, _launchd_paths
from memoreei.service._systemd import SystemdBackend, _systemd_paths

# typer.Exit subclasses click.exceptions.Exit (not SystemExit)
_AnyExit = (SystemExit, ClickExit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(*args, **kwargs):
    return MagicMock(returncode=0, stdout="", stderr="")


def _fake_launchd_paths(tmp_path: Path):
    memoreei_dir = tmp_path / ".memoreei"
    plist_path = tmp_path / "LaunchAgents" / "com.memoreei.server.plist"
    log_path = memoreei_dir / "memoreei.log"
    return memoreei_dir, plist_path, log_path


def _fake_systemd_paths(tmp_path: Path):
    memoreei_dir = tmp_path / ".memoreei"
    unit_path = tmp_path / ".config" / "systemd" / "user" / "memoreei.service"
    return memoreei_dir, unit_path


# ===========================================================================
# LaunchdBackend
# ===========================================================================


class TestLaunchdInstall:
    def test_writes_start_sh(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("MEMOREEI_DB_PATH=./memoreei.db\n")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            LaunchdBackend().install("/usr/bin/memoreei", env_path, 8080)

        start_sh = tmp_path / ".memoreei" / "start.sh"
        assert start_sh.exists()
        content = start_sh.read_text()
        assert "serve --sse --port 8080" in content
        assert str(env_path) in content
        assert content.startswith("#!/bin/bash")

    def test_start_sh_is_executable(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            LaunchdBackend().install("/usr/bin/memoreei", env_path, 8080)

        start_sh = tmp_path / ".memoreei" / "start.sh"
        assert start_sh.stat().st_mode & 0o111, "start.sh is not executable"

    def test_writes_valid_plist(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            LaunchdBackend().install("/usr/bin/memoreei", env_path, 8080)

        _, plist_path, _ = _fake_launchd_paths(tmp_path)
        assert plist_path.exists()
        content = plist_path.read_text()
        assert "com.memoreei.server" in content
        assert "<key>RunAtLoad</key>" in content
        assert "<key>KeepAlive</key>" in content
        assert "<true/>" in content

    def test_calls_launchctl_load_and_start(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_capture):
            LaunchdBackend().install("/usr/bin/memoreei", env_path, 8080)

        flat = [" ".join(c) for c in calls]
        assert any("launchctl" in s and "load" in s for s in flat)
        assert any("launchctl" in s and "start" in s for s in flat)

    def test_custom_port_in_start_sh(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            LaunchdBackend().install("/usr/bin/memoreei", env_path, 9090)

        assert "9090" in (tmp_path / ".memoreei" / "start.sh").read_text()

    def test_launchctl_load_failure_raises(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        call_n = [0]

        def _fail_on_load(*args, **kwargs):
            call_n[0] += 1
            if call_n[0] == 2:  # second call = load
                return MagicMock(returncode=1, stdout="", stderr="permission denied")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_fail_on_load), \
             pytest.raises(_AnyExit):
            LaunchdBackend().install("/usr/bin/memoreei", env_path, 8080)


class TestLaunchdUninstall:
    def test_no_plist_exits_zero(self, tmp_path):
        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             pytest.raises(_AnyExit) as exc:
            LaunchdBackend().uninstall()
        assert getattr(exc.value, "code", exc.value.args[0] if exc.value.args else 0) == 0

    def test_removes_plist(self, tmp_path):
        _, plist_path, _ = _fake_launchd_paths(tmp_path)
        plist_path.parent.mkdir(parents=True)
        plist_path.write_text("<plist/>")

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            LaunchdBackend().uninstall()

        assert not plist_path.exists()

    def test_calls_launchctl_unload(self, tmp_path):
        _, plist_path, _ = _fake_launchd_paths(tmp_path)
        plist_path.parent.mkdir(parents=True)
        plist_path.write_text("<plist/>")
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_capture):
            LaunchdBackend().uninstall()

        assert any("unload" in " ".join(c) for c in calls)


class TestLaunchdStatus:
    def test_not_running(self, capsys):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            LaunchdBackend().status()
        assert "not running" in capsys.readouterr().out.lower()

    def test_running_prints_output(self, capsys):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout='{"PID" = 12345;}', stderr="")):
            LaunchdBackend().status()
        assert "12345" in capsys.readouterr().out


class TestLaunchdLogs:
    def test_no_log_file_raises(self, tmp_path):
        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             pytest.raises(_AnyExit):
            LaunchdBackend().logs(follow=False, lines=50)

    def test_calls_tail(self, tmp_path):
        _, _, log_path = _fake_launchd_paths(tmp_path)
        log_path.parent.mkdir(parents=True)
        log_path.write_text("line1\n")
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_capture):
            LaunchdBackend().logs(follow=False, lines=50)

        assert "tail" in calls[0]
        assert str(log_path) in calls[0]

    def test_follow_adds_f_flag(self, tmp_path):
        _, _, log_path = _fake_launchd_paths(tmp_path)
        log_path.parent.mkdir(parents=True)
        log_path.write_text("line1\n")
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("memoreei.service._launchd._launchd_paths", lambda: _fake_launchd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_capture):
            LaunchdBackend().logs(follow=True, lines=50)

        assert "-f" in calls[0]


# ===========================================================================
# SystemdBackend
# ===========================================================================


class TestSystemdInstall:
    def test_writes_unit_file(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("MEMOREEI_DB_PATH=./memoreei.db\n")

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            SystemdBackend().install("/usr/bin/memoreei", env_path, 8080)

        _, unit_path = _fake_systemd_paths(tmp_path)
        assert unit_path.exists()
        content = unit_path.read_text()
        assert f"EnvironmentFile={env_path}" in content
        assert "ExecStart=/usr/bin/memoreei serve --sse --port 8080" in content
        assert "Restart=always" in content
        assert "WantedBy=default.target" in content

    def test_custom_port_in_unit(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            SystemdBackend().install("/usr/bin/memoreei", env_path, 9090)

        _, unit_path = _fake_systemd_paths(tmp_path)
        assert "port 9090" in unit_path.read_text()

    def test_calls_daemon_reload_and_enable(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_capture):
            SystemdBackend().install("/usr/bin/memoreei", env_path, 8080)

        flat = [" ".join(c) for c in calls]
        assert any("daemon-reload" in s for s in flat)
        assert any("enable" in s and "--now" in s for s in flat)

    def test_daemon_reload_failure_raises(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("")

        def _fail(*args, **kwargs):
            return MagicMock(returncode=1, stdout="", stderr="Failed to connect to bus")

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_fail), \
             pytest.raises(_AnyExit):
            SystemdBackend().install("/usr/bin/memoreei", env_path, 8080)

    def test_bus_error_prints_linger_tip(self, tmp_path, capsys):
        env_path = tmp_path / ".env"
        env_path.write_text("")

        def _fail(*args, **kwargs):
            return MagicMock(returncode=1, stdout="", stderr="Failed to connect to bus: No such file or directory")

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_fail), \
             pytest.raises(_AnyExit):
            SystemdBackend().install("/usr/bin/memoreei", env_path, 8080)

        out = capsys.readouterr().out
        assert "loginctl enable-linger" in out


class TestSystemdUninstall:
    def test_no_unit_exits_zero(self, tmp_path):
        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             pytest.raises(_AnyExit) as exc:
            SystemdBackend().uninstall()
        assert getattr(exc.value, "code", exc.value.args[0] if exc.value.args else 0) == 0

    def test_removes_unit_file(self, tmp_path):
        _, unit_path = _fake_systemd_paths(tmp_path)
        unit_path.parent.mkdir(parents=True)
        unit_path.write_text("[Unit]\n")

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_ok):
            SystemdBackend().uninstall()

        assert not unit_path.exists()

    def test_calls_disable_and_daemon_reload(self, tmp_path):
        _, unit_path = _fake_systemd_paths(tmp_path)
        unit_path.parent.mkdir(parents=True)
        unit_path.write_text("[Unit]\n")
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("memoreei.service._systemd._systemd_paths", lambda: _fake_systemd_paths(tmp_path)), \
             patch("subprocess.run", side_effect=_capture):
            SystemdBackend().uninstall()

        flat = [" ".join(c) for c in calls]
        assert any("disable" in s and "--now" in s for s in flat)
        assert any("daemon-reload" in s for s in flat)


class TestSystemdStatus:
    def test_not_running(self, capsys):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            SystemdBackend().status()
        assert "not running" in capsys.readouterr().out.lower()

    def test_inactive_but_known_shows_output(self, capsys):
        # exit code 3 = inactive, unit is known — should show output, not "not running"
        with patch("subprocess.run", return_value=MagicMock(returncode=3, stdout="● memoreei.service - inactive", stderr="")):
            SystemdBackend().status()
        assert "memoreei" in capsys.readouterr().out

    def test_running_shows_output(self, capsys):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="● memoreei.service - active (running)", stderr="")):
            SystemdBackend().status()
        assert "active" in capsys.readouterr().out


class TestSystemdLogs:
    def test_calls_journalctl(self):
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_capture):
            SystemdBackend().logs(follow=False, lines=50)

        assert "journalctl" in calls[0]
        assert "--user" in calls[0]
        assert "memoreei" in calls[0]

    def test_follow_adds_f_flag(self):
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_capture):
            SystemdBackend().logs(follow=True, lines=50)

        assert "-f" in calls[0]

    def test_no_follow_omits_f_flag(self):
        calls = []

        def _capture(*args, **kwargs):
            calls.append(args[0])
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_capture):
            SystemdBackend().logs(follow=False, lines=50)

        assert "-f" not in calls[0]
