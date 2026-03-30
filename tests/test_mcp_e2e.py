"""End-to-end MCP server tests via stdio JSON-RPC.

Adapted from scripts/test_mcp_e2e.py. These tests start the real MCP server
in a subprocess and exercise the full JSON-RPC protocol. They are skipped by
default because they require the fastembed model to download/load and can
take 30–60 seconds.

Set RUN_E2E=1 to enable:
    RUN_E2E=1 pytest tests/test_mcp_e2e.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).parent.parent
PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="e2e tests are slow (fastembed model load). Set RUN_E2E=1 to run.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send(proc: subprocess.Popen, obj: dict) -> None:
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, timeout: int = 60) -> dict:
    """Read one JSON-RPC message, handling Content-Length framing or bare JSON lines."""
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        ch = proc.stdout.read(1)
        if not ch:
            time.sleep(0.01)
            continue
        buf += ch
        if ch == b"\n":
            line = buf.strip()
            if not line:
                buf = b""
                continue
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1].strip())
                proc.stdout.read(2)  # consume \r\n separator
                body = proc.stdout.read(length)
                return json.loads(body)
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                buf = b""
                continue
    raise TimeoutError(f"No response within {timeout}s")


@pytest.fixture(scope="module")
def mcp_proc():
    """Start the MCP server subprocess for the duration of this module."""
    proc = subprocess.Popen(
        [PYTHON, "-m", "memoreei.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(PROJECT_DIR),
    )
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="module")
def initialized_proc(mcp_proc):
    """Send initialize + initialized notification; return the proc ready for tool calls."""
    _send(mcp_proc, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "0.1"},
        },
    })
    resp = _recv(mcp_proc)
    assert resp.get("id") == 1
    assert "result" in resp
    assert "capabilities" in resp["result"]

    _send(mcp_proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return mcp_proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tools_list(initialized_proc):
    _send(initialized_proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    resp = _recv(initialized_proc)
    assert resp.get("id") == 2
    tools = resp.get("result", {}).get("tools", [])
    tool_names = {t["name"] for t in tools}
    expected = {"search_memory", "get_context", "add_memory", "list_sources", "ingest_whatsapp", "sync_discord"}
    assert len(tools) == 6, f"Expected 6 tools, got {len(tools)}: {tool_names}"
    assert tool_names == expected


def test_search_memory_returns_content(initialized_proc):
    _send(initialized_proc, {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "search_memory", "arguments": {"query": "quantum bagel"}},
    })
    resp = _recv(initialized_proc, timeout=60)
    assert resp.get("id") == 3
    assert "result" in resp
    assert isinstance(resp["result"].get("content"), list)


def test_list_sources_returns_content(initialized_proc):
    _send(initialized_proc, {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "list_sources", "arguments": {}},
    })
    resp = _recv(initialized_proc, timeout=30)
    assert resp.get("id") == 4
    assert "result" in resp
    assert isinstance(resp["result"].get("content"), list)


def test_add_memory_returns_id(initialized_proc):
    _send(initialized_proc, {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "add_memory", "arguments": {"content": "e2e test note"}},
    })
    resp = _recv(initialized_proc, timeout=60)
    assert resp.get("id") == 5
    assert "result" in resp
    content = resp["result"].get("content", [])
    assert isinstance(content, list)
    # Try to find the returned memory ID
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                data = json.loads(block["text"])
                assert "id" in data
                return
            except (json.JSONDecodeError, KeyError):
                pass
