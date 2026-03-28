#!/usr/bin/env bash
# demo_setup.sh — prepare Memoreei for a live demo
# Usage: bash scripts/demo_setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="$PROJECT_DIR/.venv/bin/python"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "  ${YELLOW}[!!]${NC}  $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; FAILURES=$((FAILURES+1)); }

FAILURES=0

echo ""
echo "=== Memoreei Demo Setup ==="
echo ""

# ── 1. Virtual environment ────────────────────────────────────────────────────
echo "Checking environment..."
if [[ -x "$PYTHON" ]]; then
    ok "Virtual env found: $PYTHON"
else
    fail "Virtual env not found at $PYTHON — run: python -m venv .venv && source .venv/bin/activate && pip install -e ."
fi

# ── 2. .env file ──────────────────────────────────────────────────────────────
if [[ -f "$PROJECT_DIR/.env" ]]; then
    ok ".env file present"
else
    warn ".env not found — copying .env.example (Discord sync will need tokens)"
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env" 2>/dev/null || true
fi

# ── 3. Seed database if empty ─────────────────────────────────────────────────
echo ""
echo "Checking database..."
DB_PATH="${MEMOREEI_DB_PATH:-$PROJECT_DIR/memoreei.db}"

MEMORY_COUNT=$("$PYTHON" - "$PROJECT_DIR" <<'PYEOF' 2>/dev/null || echo "0"
import asyncio, os, sys
project_dir = sys.argv[1]
sys.path.insert(0, os.path.join(project_dir, 'src'))
from dotenv import load_dotenv
load_dotenv(os.path.join(project_dir, '.env'))
from memoreei.storage.database import Database

async def count():
    db_path = os.environ.get("MEMOREEI_DB_PATH", os.path.join(project_dir, "memoreei.db"))
    async with Database(db_path=db_path) as db:
        sources = await db.list_sources()
        total = sum(sources.values())
        print(total)

asyncio.run(count())
PYEOF
)

if [[ "$MEMORY_COUNT" -gt 0 ]] 2>/dev/null; then
    ok "Database has $MEMORY_COUNT memories — skipping seed"
else
    warn "Database is empty — seeding with sample WhatsApp data..."
    if "$PYTHON" scripts/seed_data.py; then
        ok "Database seeded successfully"
    else
        fail "Seed failed — check scripts/seed_data.py output above"
    fi
fi

# ── 4. List sources ───────────────────────────────────────────────────────────
echo ""
echo "Memory sources:"
"$PYTHON" - "$PROJECT_DIR" <<'PYEOF' 2>/dev/null || warn "Could not list sources"
import asyncio, os, sys
project_dir = sys.argv[1]
sys.path.insert(0, os.path.join(project_dir, 'src'))
from dotenv import load_dotenv
load_dotenv(os.path.join(project_dir, '.env'))
from memoreei.storage.database import Database

async def show():
    db_path = os.environ.get("MEMOREEI_DB_PATH", os.path.join(project_dir, "memoreei.db"))
    async with Database(db_path=db_path) as db:
        sources = await db.list_sources()
        if sources:
            for src, count in sorted(sources.items()):
                print(f"    {src}: {count} messages")
        else:
            print("    (none)")

asyncio.run(show())
PYEOF

# ── 5. Verify MCP server starts ───────────────────────────────────────────────
echo ""
echo "Verifying MCP server..."

MCP_TEST=$("$PYTHON" - <<'PYEOF' 2>/dev/null
import subprocess, json, time, sys

proc = subprocess.Popen(
    [sys.executable, '-m', 'memoreei.server'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

msg = json.dumps({
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'initialize',
    'params': {
        'protocolVersion': '2024-11-05',
        'capabilities': {},
        'clientInfo': {'name': 'demo-setup', 'version': '0'}
    }
}) + '\n'

proc.stdin.write(msg.encode())
proc.stdin.flush()
time.sleep(1.5)
proc.terminate()
out, _ = proc.communicate(timeout=5)

if out and b'"protocolVersion"' in out:
    print("ok")
else:
    print("fail")
PYEOF
)

if [[ "$MCP_TEST" == "ok" ]]; then
    ok "MCP server starts and responds to initialize"
else
    fail "MCP server did not respond — check: $PYTHON -m memoreei.server"
fi

# ── 6. Check .mcp.json ────────────────────────────────────────────────────────
echo ""
echo "Checking MCP client config..."
if [[ -f "$PROJECT_DIR/.mcp.json" ]]; then
    ok ".mcp.json present (Claude Code can connect)"
else
    warn ".mcp.json not found — Claude Code won't auto-connect"
    warn "  Add memoreei to .mcp.json or Claude Desktop config"
fi

# ── 7. Discord token check ────────────────────────────────────────────────────
DISCORD_TOKEN="${DISCORD_BOT_TOKEN:-}"
if [[ -z "$DISCORD_TOKEN" ]]; then
    # Try loading from .env
    DISCORD_TOKEN=$(grep -m1 '^DISCORD_BOT_TOKEN=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
fi

if [[ -n "$DISCORD_TOKEN" && "$DISCORD_TOKEN" != "your_bot_token_here" ]]; then
    ok "Discord bot token configured"
else
    warn "DISCORD_BOT_TOKEN not set — sync_discord will fail (WhatsApp demo still works)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "==========================="
if [[ "$FAILURES" -eq 0 ]]; then
    echo -e "${GREEN}All checks passed — demo is ready!${NC}"
else
    echo -e "${RED}$FAILURES check(s) failed — review output above before demoing.${NC}"
    exit 1
fi
