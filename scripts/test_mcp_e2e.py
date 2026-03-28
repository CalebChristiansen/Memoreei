#!/usr/bin/env python3
"""End-to-end test for the Memoreei MCP server via stdio JSON-RPC."""

import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python")

def send(proc, obj):
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()

def recv(proc, timeout=30):
    """Read one JSON-RPC message from stdout, handling Content-Length framing or bare JSON lines."""
    proc.stdout._timeout = timeout  # hint only
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
            # Handle Content-Length framing (LSP-style)
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1].strip())
                # consume blank line separator
                proc.stdout.read(2)  # \r\n
                body = proc.stdout.read(length)
                return json.loads(body)
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                buf = b""
                continue
    raise TimeoutError(f"No response within {timeout}s")

def check(condition, step, detail=""):
    if condition:
        print(f"  PASS  {step}")
    else:
        print(f"  FAIL  {step}" + (f": {detail}" if detail else ""))
        print("FAIL")
        sys.exit(1)

def main():
    print("Starting MCP server...")
    proc = subprocess.Popen(
        [PYTHON, "-m", "memoreei.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(PROJECT_DIR),
    )

    try:
        # Step 1: initialize
        print("\n[1] initialize")
        send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "0.1"},
            },
        })
        resp = recv(proc)
        check(resp.get("id") == 1, "initialize id matches")
        check("result" in resp, "initialize has result")
        check("capabilities" in resp.get("result", {}), "initialize capabilities present")
        print(f"    serverInfo: {resp['result'].get('serverInfo', {})}")

        # Step 2: initialized notification (no response expected)
        print("\n[2] initialized notification")
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        print("  PASS  notification sent (no response expected)")

        # Step 3: tools/list
        print("\n[3] tools/list")
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = recv(proc)
        check(resp.get("id") == 2, "tools/list id matches")
        tools = resp.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        print(f"    tools: {tool_names}")
        check(len(tools) == 6, f"6 tools registered (got {len(tools)})")
        expected = {"search_memory", "get_context", "add_memory", "list_sources", "ingest_whatsapp", "sync_discord"}
        check(set(tool_names) == expected, f"all expected tools present (got {set(tool_names)})")

        # Step 4: search_memory
        print("\n[4] search_memory(query='quantum bagel')")
        send(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_memory", "arguments": {"query": "quantum bagel"}},
        })
        resp = recv(proc, timeout=60)
        check(resp.get("id") == 3, "search_memory id matches")
        check("result" in resp, "search_memory has result")
        content = resp["result"].get("content", [])
        # The result may be a list or a text content block
        print(f"    result content blocks: {len(content)}")
        check(isinstance(content, list), "search_memory returns content list")

        # Step 5: list_sources
        print("\n[5] list_sources")
        send(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "list_sources", "arguments": {}},
        })
        resp = recv(proc, timeout=30)
        check(resp.get("id") == 4, "list_sources id matches")
        check("result" in resp, "list_sources has result")
        content = resp["result"].get("content", [])
        print(f"    result: {content}")
        check(isinstance(content, list), "list_sources returns content list")

        # Step 6: add_memory
        print("\n[6] add_memory(content='manual test note')")
        send(proc, {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "add_memory", "arguments": {"content": "manual test note"}},
        })
        resp = recv(proc, timeout=60)
        check(resp.get("id") == 5, "add_memory id matches")
        check("result" in resp, "add_memory has result")
        content = resp["result"].get("content", [])
        print(f"    result: {content}")
        check(isinstance(content, list), "add_memory returns content list")
        # Try to extract the returned ID from text content
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    data = json.loads(block["text"])
                    if "id" in data:
                        print(f"    memory id: {data['id']}")
                except (json.JSONDecodeError, KeyError):
                    pass

        print("\n" + "=" * 40)
        print("ALL PASS")

    except TimeoutError as e:
        stderr = proc.stderr.read(4096).decode(errors="replace")
        print(f"\nTimeout: {e}")
        print(f"Server stderr:\n{stderr}")
        print("FAIL")
        sys.exit(1)
    except Exception as e:
        stderr = proc.stderr.read(4096).decode(errors="replace")
        print(f"\nError: {e}")
        print(f"Server stderr:\n{stderr}")
        print("FAIL")
        sys.exit(1)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

if __name__ == "__main__":
    main()
