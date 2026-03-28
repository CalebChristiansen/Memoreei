#!/usr/bin/env python3
"""Watch #memoreei-demo for messages from Caleb and auto-sync the DB."""

import asyncio
import os
import sys
import discord
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from memoreei.connectors.discord_connector import DiscordConnector
from memoreei.storage.database import Database
from memoreei.search.embeddings import get_provider

CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 'REDACTED_CHANNEL_ID'))
CALEB_ID = REDACTED_USER_ID
SYNC_COOLDOWN = 5  # seconds between syncs

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

db = None
emb = None
last_sync = 0


async def do_sync():
    global last_sync
    now = asyncio.get_event_loop().time()
    if now - last_sync < SYNC_COOLDOWN:
        return
    last_sync = now
    try:
        dc = DiscordConnector(os.getenv('DISCORD_BOT_TOKEN'), db, emb)
        count = await dc.sync_channel(str(CHANNEL_ID))
        if count > 0:
            print(f"[sync] {count} new messages ingested")
    except Exception as e:
        print(f"[sync] error: {e}")


@client.event
async def on_ready():
    global db, emb
    db = Database()
    await db.connect()
    emb = get_provider()
    print(f"[watch] Listening on channel {CHANNEL_ID} for messages from Caleb")


@client.event
async def on_message(message):
    if message.channel.id != CHANNEL_ID:
        return
    if message.author.id == CALEB_ID:
        print(f"[watch] Caleb said: {message.content[:80]}")
        await do_sync()


client.run(os.getenv('DISCORD_BOT_TOKEN'), log_handler=None)
