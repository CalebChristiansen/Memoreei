#!/usr/bin/env python3
"""Example: Discord bot that answers memory queries via Memoreei.

Usage:
  pip install discord.py
  DISCORD_BOT_TOKEN=your-token python examples/discord_bot.py

Commands:
  !remember <query>   Search your memory database and return top results.
"""
import os
import asyncio
import discord
from memoreei.storage.database import Database
from memoreei.search.embeddings import get_provider
from memoreei.search.hybrid import HybridSearch

TOKEN = os.environ["DISCORD_BOT_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

db: Database
search: HybridSearch

@client.event
async def on_ready():
    global db, search
    db = Database(db_path='./memoreei.db')
    await db.connect()
    embedder = get_provider()
    search = HybridSearch(db=db, embedder=embedder)
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if not message.content.startswith("!remember "):
        return

    query = message.content[len("!remember "):].strip()
    if not query:
        await message.reply("Usage: `!remember <query>`")
        return

    results = await search.search(query, limit=5)
    if not results:
        await message.reply("No memories found.")
        return

    lines = []
    for r in results:
        source = r.get("source", "?")
        content = r.get("content", "")[:120]
        lines.append(f"**[{source}]** {content}")

    await message.reply("\n".join(lines))

client.run(TOKEN)
