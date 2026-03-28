#!/usr/bin/env bash
# Quick sync — pull new Discord messages into memoreei.db
cd /home/fi/.openclaw/workspace-elliot/projects/memoreei
.venv/bin/python -c "
from memoreei.connectors.discord_connector import DiscordConnector
from memoreei.storage.database import Database
from memoreei.search.embeddings import get_provider
from dotenv import load_dotenv
import os, asyncio

load_dotenv()

async def main():
    db = Database()
    await db.connect()
    emb = get_provider()
    dc = DiscordConnector(os.getenv('DISCORD_BOT_TOKEN'), db, emb)
    count = await dc.sync_channel(os.getenv('DISCORD_CHANNEL_ID'))
    print(f'Synced {count} new messages')

asyncio.run(main())
"
