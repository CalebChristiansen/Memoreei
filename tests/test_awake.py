"""Staying awake while plugged in (memoreei.awake), and the Mac app's way to Connections."""
from __future__ import annotations

import asyncio
import sys
import time

import pytest
from typer.testing import CliRunner

import memoreei.config as config_module
from memoreei import awake
from memoreei.admin import auth
from memoreei.cli import app
from memoreei.config import config_env_path
from tests.test_admin import ORIGIN, _login_token, server, signed_in  # noqa: F401  (server is a fixture)

runner = CliRunner()
HOLD = [sys.executable, "-c", "import time; time.sleep(60)"]


def _supply(root, name, **files):
    d = root / name
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / k).write_text(v + "\n")


# ── what holds off sleep, and when ──────────────────────────────────────────


def test_the_commands_end_with_this_process():
    which = lambda name: f"/usr/bin/{name}"  # noqa: E731
    assert awake.command("darwin", 42, which) == ["/usr/bin/caffeinate", "-s", "-w", "42"]
    linux = awake.command("linux", 42, which)
    assert linux[:2] == ["/usr/bin/systemd-inhibit", "--what=sleep"] and "--mode=block" in linux
    assert linux[-4:] == ["/usr/bin/tail", "--pid=42", "-f", "/dev/null"]
    assert awake.command("linux", 42, lambda name: None) is None
    assert awake.command("win32", 42, which) is None


def test_plugged_in_on_linux(tmp_path):
    assert awake.on_mains(tmp_path / "missing")  # no power_supply at all: a server
    _supply(tmp_path, "hidpp_battery_0", type="Battery", scope="Device")  # a mouse
    assert awake.on_mains(tmp_path)
    _supply(tmp_path, "BAT0", type="Battery")
    _supply(tmp_path, "AC", type="Mains", online="0")
    assert not awake.on_mains(tmp_path)
    (tmp_path / "AC" / "online").write_text("1\n")
    assert awake.on_mains(tmp_path)


def _run(coro):
    return asyncio.run(coro)


def _awake(**kw):
    state = {"setting": True, "reachable": True, "plugged": True}
    state.update(kw)
    a = awake.StayAwake(lambda: state["reachable"], platform=kw.get("platform", "linux"),
                        setting=lambda: state["setting"], plugged_in=lambda: state["plugged"],
                        make_command=kw.get("make_command", lambda: HOLD))
    return a, state


def test_it_holds_only_while_wanted_reachable_and_plugged_in():
    async def go():
        a, s = _awake(setting=False)
        await a.reconcile()
        assert a.state == "off" and a.proc is None
        s["setting"] = True
        s["reachable"] = False
        await a.reconcile()
        assert a.state == "idle" and a.proc is None
        s["reachable"] = True
        await a.reconcile()
        proc = a.proc
        assert a.state == "holding" and proc.returncode is None
        await a.reconcile()
        assert a.proc is proc  # one hold, not one per look
        s["plugged"] = False
        await a.reconcile()
        assert a.state == "battery" and a.proc is None and proc.returncode is not None
        s["plugged"] = True
        await a.reconcile()
        assert a.state == "holding"
        s["setting"] = False
        held = a.proc
        await a.reconcile()
        assert a.state == "off" and held.returncode is not None
        assert a.status() == {"on": False, "state": "off", "message": "Off: this computer sleeps as usual."}

    _run(go())


def test_on_a_mac_caffeinate_minds_the_battery_itself():
    async def go():
        a, s = _awake(platform="darwin", plugged=False)
        await a.reconcile()
        assert a.proc is not None and a.proc.returncode is None  # held, and macOS ignores it on battery
        assert a.state == "battery"
        s["plugged"] = True
        await a.reconcile()
        assert a.state == "holding"
        await a._release()

    _run(go())


def test_a_refusal_is_said_and_not_retried_every_look():
    calls = []

    def refused():
        calls.append(1)
        return [sys.executable, "-c", "import sys; sys.stderr.write('Failed to inhibit: Access denied'); sys.exit(1)"]

    async def go():
        a, _ = _awake(make_command=refused)
        await a.reconcile()
        assert a.state == "failed" and "Access denied" in a.status()["message"]
        await a.reconcile()
        assert len(calls) == 1

    _run(go())


def test_nothing_to_hold_with():
    async def go():
        a, _ = _awake(make_command=lambda: None)
        await a.reconcile()
        assert a.state == "unsupported"

    _run(go())


def test_the_run_loop_lets_go_when_stopped():
    async def go():
        a, _ = _awake()
        task = asyncio.create_task(a.run())
        for _ in range(100):
            if a.state == "holding":
                break
            await asyncio.sleep(0.05)
        proc = a.proc
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.returncode is not None

    _run(go())


def test_the_setting_is_read_from_config_env_each_time(monkeypatch):
    monkeypatch.setattr(config_module, "_process_env_keys", frozenset())
    assert not awake.setting_on()
    config_env_path().parent.mkdir(parents=True, exist_ok=True)
    config_env_path().write_text("MEMOREEI_STAY_AWAKE=1\n")
    assert awake.setting_on()


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_the_cli_switches_it(monkeypatch):
    monkeypatch.setattr(config_module, "_process_env_keys", frozenset())
    r = runner.invoke(app, ["stay-awake", "on"])
    assert r.exit_code == 0 and "on" in r.output
    assert "MEMOREEI_STAY_AWAKE=1" in config_env_path().read_text()
    assert awake.setting_on()
    r = runner.invoke(app, ["stay-awake", "off"])
    assert r.exit_code == 0 and not awake.setting_on()
    assert runner.invoke(app, ["stay-awake", "maybe"]).exit_code == 2


# ── the dashboard ────────────────────────────────────────────────────────────


@pytest.fixture
def no_real_hold(monkeypatch):
    monkeypatch.setattr(awake, "command", lambda platform, pid, which=None: HOLD)
    monkeypatch.setattr(awake, "on_mains", lambda root=None: True)
    monkeypatch.setattr(awake, "mac_on_battery", lambda: False)
    monkeypatch.setattr(config_module, "_process_env_keys", frozenset())


def test_the_switch_shows_where_chatbots_can_reach_this_computer(server, monkeypatch, no_real_hold):
    client = signed_in(server)
    assert "Stay awake while plugged in" not in client.get("/admin/connections").text  # nothing reaches it
    monkeypatch.setenv("MEMOREEI_PUBLIC_PORT", "0")
    monkeypatch.setattr(config_module, "_config", None)
    client = signed_in(server)
    for _ in range(100):
        if awake.AWAKE is not None and awake._reachable():
            break
        time.sleep(0.05)
    assert 'aria-label="Stay awake while plugged in"' in client.get("/admin/connections").text

    r = client.post("/admin/stay-awake", data={"on": "1"}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/connections#awake"
    assert awake.setting_on() and awake.AWAKE.state == "holding"
    text = client.get("/admin/connections").text
    assert 'aria-pressed="true"' in text and "stays awake while it&#39;s plugged in" in text

    client.post("/admin/stay-awake", data={"on": "0"}, headers=ORIGIN)
    assert not awake.setting_on() and awake.AWAKE.state == "off" and awake.AWAKE.proc is None


def test_the_switch_wants_the_dashboards_own_page(server, no_real_hold):
    client = signed_in(server)
    r = client.post("/admin/stay-awake", data={"on": "1"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and not awake.setting_on()


# ── the Mac app's Connections item ───────────────────────────────────────────


def test_a_login_link_can_open_a_page(server):
    client = server()
    r = client.get(f"/admin/login?token={_login_token(server.db_path)}&next=/admin/connections",
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/connections"
    for bad in ("https://evil.example/", "//evil.example/admin/", "/elsewhere"):
        r = server().get(f"/admin/login?token={_login_token(server.db_path)}&next={bad}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/"


def test_admin_url_takes_a_page():
    assert auth.login_url("t", 3679, next_path="/admin/connections").endswith("?token=t&next=/admin/connections")
    assert auth.login_url("t", 3679, next_path="/admin/") == "http://localhost:3679/admin/login?token=t"
    assert "next" not in auth.login_url("t", 3679, next_path="https://evil.example/")


def test_admin_url_cli_page(server):
    r = runner.invoke(app, ["admin-url", "--page", "connections"])
    assert r.exit_code == 0 and "&next=/admin/connections" in r.output


def test_the_switch_and_the_loop_looking_at_once_hold_once():
    spawned = []

    def counted():
        spawned.append(1)
        return HOLD

    async def go():
        a, _ = _awake(make_command=counted)
        await asyncio.gather(a.reconcile(), a.reconcile())
        assert len(spawned) == 1 and a.state == "holding"
        await a._release()

    _run(go())
