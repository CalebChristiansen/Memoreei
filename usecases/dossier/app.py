#!/usr/bin/env python3
"""
Contact Dossier — Memoreei use case app
CRM for your actual relationships. Pick a person, get their full dossier.
"""
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
_here = Path(__file__).parent
_project_root = _here.parent.parent

env_file = _project_root / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_db_raw = os.environ.get("MEMOREEI_DB_PATH", str(_project_root / "memoreei.db"))
DB_PATH = str((_project_root / _db_raw).resolve() if not Path(_db_raw).is_absolute() else Path(_db_raw))

# ── Sentiment keywords ─────────────────────────────────────────────────────────
POSITIVE_WORDS = {
    "love", "great", "awesome", "amazing", "happy", "good", "nice", "excellent",
    "wonderful", "fantastic", "best", "thanks", "thank", "glad", "fun", "cool",
    "perfect", "beautiful", "brilliant", "enjoy", "enjoyed", "exciting", "excited",
    "win", "winning", "won", "yay", "wow", "absolutely", "hype", "hyped", "yes",
    "agree", "exactly", "definitely", "sweet", "helpful", "appreciate", "support",
    "celebrate", "proud", "lucky", "delightful", "cheerful", "hopeful",
}

NEGATIVE_WORDS = {
    "hate", "bad", "awful", "terrible", "sad", "angry", "frustrated", "worst",
    "ugh", "sorry", "unfortunately", "problem", "issue", "broken", "fail",
    "failed", "error", "wrong", "hurt", "pain", "boring", "annoying", "annoyed",
    "disappointing", "disappointed", "cry", "crying", "miss", "missed", "lost",
    "forget", "forgot", "stress", "stressed", "tired", "exhausted", "sick",
    "difficult", "struggle", "struggling", "confusing", "confused", "stuck",
}

STOP_WORDS = {
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "he", "him", "his", "she", "her", "hers", "it", "its",
    "they", "them", "their", "what", "which", "who", "this", "that", "these",
    "those", "am", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "shall", "can", "a", "an", "the", "and", "but", "if", "or", "as",
    "at", "by", "for", "of", "on", "to", "in", "with", "from", "up", "out",
    "so", "yet", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "not", "only", "own", "same", "than", "too", "very", "just", "any",
    "all", "also", "into", "about", "after", "before", "over", "then", "there",
    "when", "where", "while", "how", "get", "got", "like", "go", "going", "gone",
    "see", "said", "say", "one", "two", "know", "think", "come", "back", "well",
    "make", "made", "want", "need", "even", "still", "here", "now", "much",
    "https", "http", "www", "com", "re", "ve", "ll", "don", "didn", "can", "t",
    "s", "m", "d", "lol", "yeah", "ok", "okay", "yep", "nope", "hey", "oh",
    "hmm", "haha", "hahaha", "omg", "btw", "imo", "irl", "tbh", "ngl", "tbf",
}

SOURCE_COLORS = {
    "discord": "#5865F2",
    "telegram": "#229ED9",
    "matrix": "#0DBD8B",
    "slack": "#E01E5A",
    "email": "#EA4335",
    "whatsapp": "#25D366",
    "mastodon": "#6364FF",
    "manual": "#888888",
}


# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_people() -> list[dict]:
    """Extract all unique participants with message counts."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT participants, source, COUNT(*) as cnt FROM memories GROUP BY participants, source"
        ).fetchall()
    finally:
        conn.close()

    people: dict[str, dict] = {}
    for row in rows:
        try:
            parts = json.loads(row["participants"] or "[]")
        except Exception:
            continue
        for p in parts:
            p = p.strip()
            if not p or len(p) < 2:
                continue
            if p not in people:
                people[p] = {"name": p, "count": 0, "sources": set()}
            people[p]["count"] += row["cnt"]
            src = (row["source"] or "unknown").split(":")[0]
            people[p]["sources"].add(src)

    result = [
        {"name": k, "count": v["count"], "sources": sorted(v["sources"])}
        for k, v in people.items()
        if v["count"] > 0
    ]
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def get_person_messages(name: str, limit: int = 500) -> list[dict]:
    """Get all messages involving a person."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, content, source, participants, ts, metadata, summary
            FROM memories
            WHERE participants LIKE ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (f'%{name}%', limit),
        ).fetchall()
    finally:
        conn.close()

    messages = []
    for row in rows:
        try:
            parts = json.loads(row["participants"] or "[]")
        except Exception:
            parts = []
        # Confirm exact match (not just substring of another name)
        if not any(p.strip() == name for p in parts):
            continue
        messages.append(dict(row))
    return messages


def analyze_topics(messages: list[dict], top_n: int = 25) -> list[dict]:
    """Extract top keywords from message content."""
    word_counts: Counter = Counter()
    for msg in messages:
        text = msg.get("content", "") or ""
        if ": " in text:
            parts = text.split(": ", 1)
            if len(parts[0]) < 40:
                text = parts[1]
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        for w in words:
            if w not in STOP_WORDS and len(w) >= 3:
                word_counts[w] += 1
    return [{"word": w, "count": c} for w, c in word_counts.most_common(top_n)]


def analyze_sentiment(messages: list[dict]) -> dict:
    """Simple sentiment analysis based on keyword matching."""
    pos_count = 0
    neg_count = 0

    for msg in messages:
        text = msg.get("content", "") or ""
        words = set(re.findall(r"\b[a-z]{2,}\b", text.lower()))
        pos_count += len(words & POSITIVE_WORDS)
        neg_count += len(words & NEGATIVE_WORDS)

    total = pos_count + neg_count
    if total == 0:
        return {"label": "Neutral", "score": 50, "pos": 0, "neg": 0, "cls": "neutral"}

    pos_pct = (pos_count / total) * 100
    if pos_pct >= 70:
        label, cls = "Very Positive", "very-positive"
    elif pos_pct >= 55:
        label, cls = "Positive", "positive"
    elif pos_pct >= 40:
        label, cls = "Neutral", "neutral"
    elif pos_pct >= 25:
        label, cls = "Mixed", "mixed"
    else:
        label, cls = "Negative", "negative"

    return {
        "label": label,
        "score": int(pos_pct),
        "pos": pos_count,
        "neg": neg_count,
        "cls": cls,
    }


def build_dossier(name: str) -> dict:
    """Build a full dossier for a person."""
    messages = get_person_messages(name)

    if not messages:
        return {"name": name, "found": False}

    ts_list = [m["ts"] for m in messages if m.get("ts")]
    first_ts = min(ts_list) if ts_list else None
    last_ts = max(ts_list) if ts_list else None
    first_date = datetime.fromtimestamp(first_ts).strftime("%b %d, %Y") if first_ts else "Unknown"
    last_date = datetime.fromtimestamp(last_ts).strftime("%b %d, %Y") if last_ts else "Unknown"

    source_counts: Counter = Counter()
    for m in messages:
        src = (m.get("source") or "unknown").split(":")[0]
        source_counts[src] += 1

    topics = analyze_topics(messages)
    sentiment = analyze_sentiment(messages)

    # Build timeline grouped by source platform
    by_source: dict[str, list] = {}
    for m in messages:
        src_full = m.get("source") or "unknown"
        src_key = src_full.split(":")[0]
        if src_key not in by_source:
            by_source[src_key] = []

        content = m.get("content") or ""
        ts = m.get("ts") or 0
        date_str = datetime.fromtimestamp(ts).strftime("%b %d, %Y %H:%M") if ts else ""

        speaker = name
        display_content = content
        if ": " in content:
            parts = content.split(": ", 1)
            if len(parts[0]) < 40 and not parts[0].startswith("http"):
                speaker = parts[0]
                display_content = parts[1]

        by_source[src_key].append({
            "id": m.get("id", ""),
            "content": display_content[:500],
            "speaker": speaker,
            "ts": ts,
            "date": date_str,
            "source": src_key,
        })

    for src in by_source:
        by_source[src].sort(key=lambda x: x["ts"])

    # Recent quotes feed (last 30 overall)
    all_sorted = sorted(messages, key=lambda x: x.get("ts") or 0, reverse=True)
    recent_quotes = []
    for m in all_sorted[:30]:
        content = m.get("content") or ""
        ts = m.get("ts") or 0
        src = (m.get("source") or "unknown").split(":")[0]
        date_str = datetime.fromtimestamp(ts).strftime("%b %d, %Y %H:%M") if ts else ""
        speaker = name
        if ": " in content:
            parts = content.split(": ", 1)
            if len(parts[0]) < 40:
                speaker = parts[0]
                content = parts[1]
        recent_quotes.append({
            "content": content[:400],
            "speaker": speaker,
            "date": date_str,
            "source": src,
            "ts": ts,
        })

    return {
        "name": name,
        "found": True,
        "total_messages": len(messages),
        "first_seen": first_date,
        "last_seen": last_date,
        "sources": dict(source_counts.most_common()),
        "source_count": len(source_counts),
        "topics": topics,
        "sentiment": sentiment,
        "timeline": by_source,
        "recent_quotes": recent_quotes,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    people = get_all_people()
    return render_template("index.html", people=people, source_colors=SOURCE_COLORS)


@app.route("/api/people")
def api_people():
    return jsonify(get_all_people())


@app.route("/api/dossier/<path:name>")
def api_dossier(name: str):
    dossier = build_dossier(name)
    # Add rank info
    if dossier.get("found"):
        people = get_all_people()
        for i, p in enumerate(people):
            if p["name"] == name:
                dossier["rank"] = i + 1
                dossier["total_people"] = len(people)
                break
    return jsonify(dossier)


@app.route("/api/refresh")
def api_refresh():
    """Trigger an incremental Discord sync and return counts."""
    import asyncio
    try:
        from memoreei.connectors.discord_connector import sync_discord
        from memoreei.search.embeddings import get_provider
        from memoreei.storage.database import Database

        async def do_refresh():
            db = Database(db_path=DB_PATH)
            await db.connect()
            embedder = get_provider()
            return await sync_discord(db=db, embedder=embedder)

        result = asyncio.run(do_refresh())
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/health")
def health():
    try:
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        return jsonify({"status": "ok", "memories": count, "db": DB_PATH})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("DOSSIER_PORT", "5051"))
    print(f"Contact Dossier starting on http://0.0.0.0:{port}")
    print(f"  DB: {DB_PATH}")
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("DEBUG")))
