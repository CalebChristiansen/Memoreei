"""Live end-to-end test: post a Discord message, sync, search, verify."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

# Allow importing from src/
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

load_dotenv(Path(__file__).parent.parent / ".env")

CHANNEL_ID = "REDACTED_CHANNEL_ID"
TEST_MESSAGE = "LIVE_TEST: the quantum bagel theory has been confirmed by the department of imaginary physics"
DISCORD_API_BASE = "https://discord.com/api/v10"


async def post_message(token: str, channel_id: str, content: str) -> str:
    """Post a message and return its ID."""
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={"content": content}) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Failed to post message ({resp.status}): {text}")
            data = await resp.json()
            return data["id"]


async def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print("FAIL: DISCORD_BOT_TOKEN not set")
        sys.exit(1)

    db_path = os.environ.get("MEMOREEI_DB_PATH", "./memoreei.db")

    from memoreei.storage.database import Database
    from memoreei.search.embeddings import get_provider
    from memoreei.connectors.discord_connector import DiscordConnector
    from memoreei.search.hybrid import HybridSearch

    # Step 1: Post message
    print(f"Posting message to channel {CHANNEL_ID}...")
    msg_id = await post_message(token, CHANNEL_ID, TEST_MESSAGE)
    print(f"Posted message ID: {msg_id}")

    # Step 2: Wait 2 seconds
    print("Waiting 2 seconds...")
    await asyncio.sleep(2)

    # Step 3: Connect to DB, sync Discord
    print("Connecting to DB and syncing Discord...")
    embedder = get_provider()
    async with Database(db_path) as db:
        connector = DiscordConnector(token=token, db=db, embedder=embedder)
        count = await connector.sync_channel(CHANNEL_ID)
        print(f"Synced {count} new messages")

        # Step 4: Search for 'quantum bagel'
        print("Searching for 'quantum bagel'...")
        searcher = HybridSearch(db=db, embedder=embedder)
        results = await searcher.search("quantum bagel", limit=10)

        # Step 5: Verify
        found = any("quantum bagel" in r.get("content", "").lower() for r in results)
        if found:
            print("PASS")
        else:
            print(f"FAIL: 'quantum bagel' not found in {len(results)} results")
            for r in results:
                print(f"  - {r.get('content', '')[:80]}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
