"""The Linux packages' side of memoreei: the shipped unit, `open`, the headless
`admin-url`, the port check, and what the dashboard shows on Linux."""
from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import memoreei.admin.app as admin_app
from memoreei import updates
from memoreei.cli import app
from memoreei.service import _port
from memoreei.service import _systemd as sd

from .test_admin import ORIGIN, server, signed_in  # noqa: F401  (the fixture)

runner = CliRunner()


@pytest.fixture
def linux(monkeypatch):
    """The dashboard as it is on Linux, with a user unit the test controls."""
    monkeypatch.setattr(admin_app, "_on_mac", lambda: False)
    monkeypatch.setattr(admin_app.sys, "platform", "linux")
    state = {"installed": True, "enabled": False, "active": True, "linger": False,
             "linger_command": "sudo loginctl enable-linger zezima"}

    async def service():
        return dict(state)

    monkeypatch.setattr(admin_app, "_service_context", service)
    return state


# ── Dashboard ────────────────────────────────────────────────────────────────


def test_linux_sources_offer_no_imessage_and_explain_signal(server, linux):
    text = signed_in(server).get("/admin/sources").text
    assert "iMessage" not in text
    assert "Signal locks its key in your keyring" in text
    assert "memoreei setup" in text
    assert signed_in(server).get("/admin/sources/imessage").status_code == 404


def test_status_shows_the_service_switches(server, linux):
    text = signed_in(server).get("/admin/").text
    assert "Start at login" in text and "Start at boot" in text
    assert "Stop Memoreei" in text and "/admin/log" in text


def test_start_at_login_switch(server, linux, monkeypatch, isolated_home):
    calls = []
    monkeypatch.setattr(sd, "set_enabled", lambda on: calls.append(on) or True)
    r = signed_in(server).post("/admin/service/autostart", data={"on": "1"}, headers=ORIGIN,
                               follow_redirects=False)
    assert r.status_code == 303 and calls == [True]
    # From now on `memoreei open` leaves the choice alone.
    assert (isolated_home / ".start-at-login-chosen").exists()


def test_linger_that_needs_an_admin_says_who_and_what(server, linux, monkeypatch):
    monkeypatch.setattr(sd, "enable_linger", lambda: False)
    client = signed_in(server)
    r = client.post("/admin/service/linger", headers=ORIGIN, follow_redirects=False)
    assert r.headers["location"] == "/admin/?linger=failed"
    assert "sudo loginctl enable-linger zezima" in client.get("/admin/?linger=failed").text


def test_stop_answers_first_then_stops(server, linux, monkeypatch):
    stopped = []
    monkeypatch.setattr(sd, "running_as_unit", lambda: True)
    monkeypatch.setattr(sd, "stop_soon", lambda: stopped.append(True))
    r = signed_in(server).post("/admin/service/stop", headers=ORIGIN)
    assert "Memoreei is taking a nap" in r.text
    assert stopped == [True]


def test_log_page_shows_the_journal(server, linux, monkeypatch):
    monkeypatch.setattr(sd, "journal", lambda lines: "memoreei: serving http://0.0.0.0:3679/mcp\n")
    assert "serving http://0.0.0.0:3679/mcp" in signed_in(server).get("/admin/log").text


def test_update_notice(server, linux, monkeypatch):
    monkeypatch.setattr(updates, "_available", {"version": "9.9.9", "url": "https://example.com/r"})
    text = signed_in(server).get("/admin/").text
    assert "Memoreei 9.9.9 is out" in text and "https://example.com/r" in text


def test_old_data_is_pointed_at_not_moved(server, linux, monkeypatch, tmp_path):
    old = tmp_path / ".memoreei"
    monkeypatch.setattr(admin_app, "legacy_home", lambda: old)
    text = signed_in(server).get("/admin/").text
    assert f"MEMOREEI_HOME={old}" in text


@pytest.mark.parametrize("newer, older", [
    ("0.4.0", "0.3.1"), ("0.4.0", "0.4.0rc2"), ("0.4.0rc2", "0.4.0rc1"),
    ("v0.4.1", "0.4.0"), ("0.10.0", "0.9.9"), ("0.4.0rc1", "0.4.0b3"),
])
def test_versions_compare_as_pep_440(newer, older):
    assert updates.is_newer(newer, older) and not updates.is_newer(older, newer)


# ── The port ─────────────────────────────────────────────────────────────────


@pytest.fixture
def held_port(monkeypatch, port_is_free):
    monkeypatch.setattr(_port, "port_free", port_is_free)  # the real check, for these
    # A listener that never answers HTTP: asking it would only wait out the timeout.
    monkeypatch.setattr(_port, "is_memoreei", lambda port: False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        yield s.getsockname()[1]


@pytest.mark.skipif(not Path("/proc/net/tcp").exists(), reason="Linux only")
def test_port_owner_reads_the_uid_from_proc(held_port):
    assert _port.port_owner(held_port) == os.getuid()
    assert not _port.port_free("127.0.0.1", held_port)
    assert _port.describe_holder(held_port) == "another of your programs"


def test_serve_refuses_a_taken_port_with_a_code_that_stops_restarts(held_port):
    result = runner.invoke(app, ["serve", "--http", "--host", "127.0.0.1", "--port", str(held_port)])
    assert result.exit_code == sd.EXIT_PORT_IN_USE == 75
    assert f"port {held_port} is in use" in result.output and "MEMOREEI_PORT" in result.output


# ── admin-url and open ───────────────────────────────────────────────────────


def _headless(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def test_admin_url_headless_prints_the_tunnel(monkeypatch):
    _headless(monkeypatch)
    result = runner.invoke(app, ["admin-url"])
    assert result.exit_code == 0, result.output
    assert "ssh -L 3679:localhost:3679 " in result.output
    assert "http://localhost:3679/admin/login?token=" in result.output


def test_admin_url_with_a_display_is_just_the_link(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    assert "ssh -L" not in runner.invoke(app, ["admin-url"]).output


@pytest.fixture
def desktop(monkeypatch):
    """`memoreei open` on a Linux desktop, with the service and browser faked."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    told, opened = [], []
    monkeypatch.setattr("memoreei.cli._tell", lambda title, text: told.append((title, text)))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: opened.append((cmd, kw)))
    monkeypatch.setattr(_port, "is_memoreei", lambda port: True)
    return told, opened


def test_open_starts_the_service_then_opens_a_login_link(desktop, monkeypatch, isolated_home):
    told, opened = desktop
    owners = iter([None, None, os.getuid()])
    monkeypatch.setattr(_port, "port_owner", lambda port: next(owners))
    monkeypatch.setattr(sd, "manager_available", lambda: True)
    monkeypatch.setattr(sd, "unit_state", lambda: {"installed": True, "enabled": False, "active": True})
    started = []
    monkeypatch.setattr(sd, "start", lambda: started.append("enable --now") or MagicMock(returncode=0))
    monkeypatch.setenv("MEMOREEI_BUNDLE", "/opt/memoreei")
    result = runner.invoke(app, ["open"])
    assert result.exit_code == 0, result.output
    assert started == ["enable --now"] and not told
    (cmd, kw), = opened
    assert cmd[0] == "/usr/bin/xdg-open" and "/admin/login?token=" in cmd[1]
    assert "MEMOREEI_BUNDLE" not in kw["env"]  # the browser doesn't inherit the bundle's settings
    assert (isolated_home / ".start-at-login-chosen").exists()


def test_open_after_the_first_time_starts_without_enabling(desktop, monkeypatch, isolated_home):
    (isolated_home / ".start-at-login-chosen").touch()
    owners = iter([None, os.getuid()])
    monkeypatch.setattr(_port, "port_owner", lambda port: next(owners))
    monkeypatch.setattr(sd, "manager_available", lambda: True)
    monkeypatch.setattr(sd, "unit_state", lambda: {"installed": True, "enabled": False, "active": True})
    calls = []
    monkeypatch.setattr(sd, "systemctl", lambda *a: calls.append(a) or MagicMock(returncode=0))
    monkeypatch.setattr(sd, "start", lambda: pytest.fail("enabled it again"))
    assert runner.invoke(app, ["open"]).exit_code == 0
    assert calls == [("start", sd.UNIT)]


def test_open_when_another_user_holds_the_port(desktop, monkeypatch):
    told, opened = desktop
    monkeypatch.setattr(_port, "port_owner", lambda port: os.getuid() + 1)
    monkeypatch.setattr(_port, "user_name", lambda uid: "hans")
    result = runner.invoke(app, ["open"])
    assert result.exit_code == 75 and not opened
    (title, text), = told
    assert title == "Port 3679 is taken"
    assert "another Memoreei, belonging to hans" in text and "MEMOREEI_PORT=3680" in text


def test_open_says_why_the_service_didnt_start(desktop, monkeypatch):
    told, opened = desktop
    monkeypatch.setattr(_port, "port_owner", lambda port: None)
    monkeypatch.setattr(sd, "manager_available", lambda: True)
    states = iter([{"installed": True, "enabled": True, "active": False}] * 2)
    monkeypatch.setattr(sd, "unit_state", lambda: next(states))
    monkeypatch.setattr(sd, "start", lambda: MagicMock(returncode=0))
    monkeypatch.setattr(sd, "journal", lambda n: "boom: no such table\n")
    assert runner.invoke(app, ["open"]).exit_code == 1
    assert told[0][0] == "Memoreei couldn't start" and "boom" in told[0][1]


def test_open_without_a_service(desktop, monkeypatch):
    told, _ = desktop
    monkeypatch.setattr(_port, "port_owner", lambda port: None)
    monkeypatch.setattr(sd, "manager_available", lambda: False)
    assert runner.invoke(app, ["open"]).exit_code == 1
    assert "memoreei service install" in told[0][1]


# ── The unit ─────────────────────────────────────────────────────────────────


def test_unit_state_reads_systemctl_show(monkeypatch):
    out = "LoadState=loaded\nUnitFileState=enabled\nActiveState=inactive\n"
    monkeypatch.setattr(sd, "systemctl", lambda *a: MagicMock(returncode=0, stdout=out))
    assert sd.unit_state() == {"installed": True, "enabled": True, "active": False}


def test_packaged_install_enables_the_shipped_unit(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMOREEI_BUNDLE", "/opt/memoreei")
    unit = tmp_path / "memoreei.service"
    unit.write_text("[Service]\nExecStart=/opt/venvs/zezima/bin/memoreei serve --http\n")
    monkeypatch.setattr(sd, "_systemd_paths", lambda: (tmp_path, unit))
    calls = []
    monkeypatch.setattr(sd, "systemctl", lambda *a: calls.append(a) or MagicMock(returncode=0))
    monkeypatch.setattr(sd, "linger", lambda: True)
    monkeypatch.setattr(sd, "wait_until_listening", lambda port: True)
    monkeypatch.setattr("memoreei.service._systemd.print_installed", lambda port: None)
    sd.SystemdBackend().install("/unused", tmp_path / "config.env", 3679)
    # The pip install's unit would shadow the package's: moved aside, not deleted.
    assert not unit.exists() and (tmp_path / "memoreei.service.pip-install").exists()
    assert ("enable", "--now", sd.UNIT) in calls


def test_packaged_service_install_needs_no_config_env(monkeypatch):
    monkeypatch.setenv("MEMOREEI_BUNDLE", "/opt/memoreei")
    backend = MagicMock()
    monkeypatch.setattr("memoreei.service._detect.get_backend", lambda: backend)
    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0, result.output
    backend.install.assert_called_once()


def test_bundle_env_file_and_unit_agree():
    """linux/memoreei.sh and the unit read the same env file, so they find the same home."""
    root = Path(__file__).parent.parent / "linux"
    assert "EnvironmentFile=-%E/memoreei/env" in (root / "memoreei.service").read_text()
    assert "${XDG_CONFIG_HOME:-$HOME/.config}/memoreei/env" in (root / "memoreei.sh").read_text()


def test_journal_is_time_and_message(monkeypatch):
    lines = "\n".join([
        '{"__REALTIME_TIMESTAMP": "1790000000000000", "MESSAGE": "memoreei: serving"}',
        '{"__REALTIME_TIMESTAMP": "1790000001000000", "MESSAGE": [104, 105, 255]}',
        "not json",
    ])
    monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(returncode=0, stdout=lines))
    out = sd.journal(10).splitlines()
    assert len(out) == 2
    assert out[0].endswith("  memoreei: serving") and out[1].endswith("  hi�")


def test_packaged_install_reports_a_server_that_didnt_start(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MEMOREEI_BUNDLE", "/opt/memoreei")
    monkeypatch.setattr(sd, "_systemd_paths", lambda: (tmp_path, tmp_path / "memoreei.service"))
    monkeypatch.setattr(sd, "systemctl", lambda *a: MagicMock(returncode=0))
    monkeypatch.setattr(sd, "wait_until_listening", lambda port: False)
    monkeypatch.setattr(sd, "journal", lambda n: "memoreei: port 3679 is in use by hans\n")
    with pytest.raises(Exception):
        sd.SystemdBackend().install("/unused", tmp_path / "config.env", 3679)
    out = capsys.readouterr().out
    assert "didn't start" in out and "in use by hans" in out and "✓" not in out
