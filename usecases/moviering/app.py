#!/usr/bin/env python3
"""
Movie Ring — Memoreei use case app
Scans your conversations for movie mentions, shows them with TMDB posters and friend quotes.
"""
import os
import re
import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from functools import lru_cache

import requests
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_here = Path(__file__).parent
_project_root = _here.parent.parent

# Load .env from project root
env_file = _project_root / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_db_raw = os.environ.get("MEMOREEI_DB_PATH", str(_project_root / "memoreei.db"))
DB_PATH = str((_project_root / _db_raw).resolve() if not Path(_db_raw).is_absolute() else Path(_db_raw))
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"

# ── Movie detection ────────────────────────────────────────────────────────────
TITLE_PATTERNS = [
    r'\b(The\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,4})\b',
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,5})\b',  # Multi-word capitalized phrase (2+ words)
    r'(?:watched?|watching|seen?|loved?|recommend(?:ed)?|rewatch|finished)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,4})',
    r'"([A-Z][^"]{2,50})"',
    r"'([A-Z][^']{2,50})'",
    r'(?:end of|beginning of|like|loved?|hate[sd]?|enjoyed?)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})',
    r'(?:movie|film|book|adaptation)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,5})',
]

# Known single-word movie titles to look for explicitly (TMDB matches too many false positives on single words)
KNOWN_SINGLE_WORD_MOVIES = {
    "inception", "arrival", "oppenheimer", "interstellar", "gravity", "jaws",
    "alien", "aliens", "gladiator", "braveheart", "amadeus", "casablanca",
    "psycho", "vertigo", "halloween", "scream", "coco", "frozen",
    "ratatouille", "shrek", "moana", "encanto", "parasite",
    "joker", "dunkirk", "tenet", "memento", "rocky", "creed",
    "dune", "her", "lucy", "fury", "babel", "troy", "heat",
    "drive", "taken", "hugo", "blitz", "tusk",
}

# Additional false-positive movie titles to filter out after TMDB lookup
TMDB_TITLE_BLOCKLIST = {
    "you are alive!", "the future", "i see you", "pizza wars: the movie",
    "alien",  # too easily matched from "alien language" etc.
    "machine learning",
}

MOVIE_FTS = "Matrix OR Inception OR movie OR film OR watched OR cinema OR netflix OR streaming OR sequel OR prequel OR trilogy OR directed OR starring OR screenplay"

STOP_TITLES = {
    "i", "it", "the", "a", "an", "and", "or", "but", "is", "was", "are", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need", "dare",
    "this", "that", "these", "those", "what", "which", "who", "whom", "whose",
    "when", "where", "why", "how", "all", "both", "each", "every", "few", "more",
    "most", "other", "some", "such", "no", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "but", "if", "we", "they", "he", "she",
    "we are", "she's not",
    # Common false positives
    "joseph gordon", "joseph gordon-levitt", "gordon-levitt",
    "cillian murphy", "amy adams", "ralph fiennes", "daniel craig",
    "ryan gosling", "andy weir", "wes anderson", "hans zimmer",
    "oscar", "oscars", "imax", "netflix", "youtube",
    "discord", "telegram", "matrix", "slack",
    "also", "here", "like", "really", "actually", "genuinely",
    "still", "ever", "never", "always", "everything", "something",
    "what", "which", "where", "about", "after", "before",
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_movie_messages(limit: int = 1000) -> list[dict]:
    """Pull ALL messages from the DB so we don't miss any movie references.
    
    Previous approach used FTS keyword matching which missed movies not in the
    hardcoded list. Since we do TMDB lookups on candidates anyway, it's better
    to scan everything and let the title extraction + TMDB matching filter.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, content, source, participants, ts, metadata
            FROM memories
            ORDER BY ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def extract_candidates(text: str) -> list[str]:
    """Extract potential movie title strings from a message."""
    found = []
    # First: regex-based extraction (multi-word phrases)
    for pat in TITLE_PATTERNS:
        for match in re.finditer(pat, text):
            title = match.group(1).strip().rstrip(".,!?;:")
            words = title.split()
            if len(words) < 1 or len(title) < 3 or len(title) > 60:
                continue
            if title.lower() in STOP_TITLES:
                continue
            # Skip single-word matches from regex (too many false positives)
            if len(words) == 1:
                continue
            if title[0].isupper():
                found.append(title)
    # Second: check for known single-word movie titles explicitly
    text_lower = text.lower()
    for movie in KNOWN_SINGLE_WORD_MOVIES:
        if re.search(r'\b' + re.escape(movie) + r'\b', text_lower):
            # Use proper capitalization
            found.append(movie.title())
    return list(dict.fromkeys(found))


@lru_cache(maxsize=500)
def tmdb_search(title: str) -> dict | None:
    """Search TMDB for a movie title. Cached."""
    if not TMDB_API_KEY:
        return None
    try:
        r = requests.get(
            f"{TMDB_BASE}/search/movie",
            params={"api_key": TMDB_API_KEY, "query": title, "language": "en-US"},
            timeout=6,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        m = results[0]
        # Relevance check: TMDB title must be reasonably close to our query
        tmdb_title = (m.get("title") or "").lower()
        query_lower = title.lower()
        if query_lower not in tmdb_title and tmdb_title not in query_lower:
            # Check word overlap
            query_words = set(query_lower.split())
            title_words = set(tmdb_title.split())
            overlap = query_words & title_words
            if len(overlap) < max(1, len(query_words) // 2):
                return None
        return {
            "tmdb_id": m.get("id"),
            "title": m.get("title", title),
            "overview": (m.get("overview") or "")[:300],
            "poster_url": f"{TMDB_IMG}{m['poster_path']}" if m.get("poster_path") else None,
            "year": (m.get("release_date") or "")[:4] or "?",
            "rating": round(m.get("vote_average", 0), 1),
            "tmdb_url": f"https://www.themoviedb.org/movie/{m.get('id')}",
        }
    except Exception:
        return None


def parse_speaker(content: str, participants: list[str]) -> str:
    if not content:
        return participants[0] if participants else "Unknown"
    if ": " in content:
        name = content.split(": ", 1)[0]
        if len(name) < 40 and not name.startswith("http"):
            return name
    return participants[0] if participants else "Unknown"


def strip_speaker(content: str) -> str:
    if ": " in content:
        parts = content.split(": ", 1)
        if len(parts[0]) < 40 and not parts[0].startswith("http"):
            return parts[1]
    return content


def fmt_source(source: str) -> str:
    if not source:
        return "Unknown"
    parts = source.split(":", 1)
    platform = parts[0].capitalize()
    label = parts[1] if len(parts) > 1 else ""
    return f"{platform}" + (f" · {label}" if label else "")


def fmt_platform(source: str) -> str:
    return (source or "unknown").split(":")[0].lower()


def _build_mention(msg: dict) -> dict:
    """Build a mention dict from a raw message row."""
    content = msg.get("content", "")
    participants = json.loads(msg.get("participants") or "[]")
    speaker = parse_speaker(content, participants)
    quote = strip_speaker(content)
    ts = msg.get("ts") or 0
    date_str = datetime.fromtimestamp(ts).strftime("%b %d, %Y") if ts else "Unknown"
    source = msg.get("source") or ""
    return {
        "id": msg.get("id", ""),
        "quote": quote,
        "speaker": speaker,
        "source": fmt_source(source),
        "platform": fmt_platform(source),
        "date": date_str,
        "ts": ts,
    }


def process_messages(messages: list[dict], sort_by: str = "mentions") -> tuple[list[dict], list[dict]]:
    """
    Returns (movie_cards, quote_cards) with aggregated mention stats.
    movie_cards sorted by sort_by: 'mentions' | 'recent' | 'people'
    """
    # tmdb_id -> aggregated data
    movie_data: dict[int, dict] = {}
    quote_cards: list[dict] = []
    seen_message_ids: set[str] = set()

    for msg in messages:
        msg_id = msg.get("id", "")
        if msg_id in seen_message_ids:
            continue
        seen_message_ids.add(msg_id)

        content = msg.get("content", "")
        # Strip speaker prefix (e.g. "Dr. Robert Ford: ...") before extracting
        if ": " in content:
            parts = content.split(": ", 1)
            if len(parts[0]) < 40 and not parts[0].startswith("http"):
                content_for_extract = parts[1]
            else:
                content_for_extract = content
        else:
            content_for_extract = content
        mention = _build_mention(msg)

        matched = False
        if TMDB_API_KEY:
            candidates = extract_candidates(content_for_extract)
            for candidate in candidates[:6]:
                info = tmdb_search(candidate)
                if info and info["title"].lower() not in TMDB_TITLE_BLOCKLIST:
                    tid = info["tmdb_id"]
                    if tid not in movie_data:
                        movie_data[tid] = {
                            "movie": info,
                            "mentions": [],
                            "seen_msg_ids": set(),
                        }
                    if msg_id not in movie_data[tid]["seen_msg_ids"]:
                        movie_data[tid]["mentions"].append(mention)
                        movie_data[tid]["seen_msg_ids"].add(msg_id)
                    matched = True
                    break

        if not matched:
            quote_cards.append(mention)

    # Phase 2: for each discovered movie, scan ALL DB messages for title mentions
    # This catches cases where regex failed to extract the title from a message.
    if movie_data and TMDB_API_KEY:
        conn = get_db()
        try:
            for tid, data in movie_data.items():
                title = data["movie"]["title"]
                rows = conn.execute(
                    "SELECT id, content, source, participants, ts, metadata FROM memories"
                    " WHERE content LIKE ? COLLATE NOCASE",
                    (f"%{title}%",),
                ).fetchall()
                for row in rows:
                    row_dict = dict(row)
                    row_id = row_dict.get("id", "")
                    if row_id not in data["seen_msg_ids"]:
                        data["mentions"].append(_build_mention(row_dict))
                        data["seen_msg_ids"].add(row_id)
                        # Remove from quote_cards if it ended up there
                        quote_cards[:] = [q for q in quote_cards if q["id"] != row_id]
        finally:
            conn.close()

    # Build final movie cards with aggregated stats
    movie_cards = []
    for tid, data in movie_data.items():
        mentions = data["mentions"]
        # Sort mentions by timestamp ascending for first/last
        mentions_sorted = sorted(mentions, key=lambda m: m["ts"] or 0)

        speakers = [m["speaker"] for m in mentions]
        unique_people = list(dict.fromkeys(speakers))

        timestamps = [m["ts"] for m in mentions if m["ts"]]
        first_seen = datetime.fromtimestamp(min(timestamps)).strftime("%b %d, %Y") if timestamps else "Unknown"
        last_seen = datetime.fromtimestamp(max(timestamps)).strftime("%b %d, %Y") if timestamps else "Unknown"
        last_ts = max(timestamps) if timestamps else 0

        # Group by person
        by_person: dict[str, list] = {}
        for m in mentions:
            by_person.setdefault(m["speaker"], []).append(m)

        movie_cards.append({
            "movie": data["movie"],
            "mentions": mentions,
            "mention_count": len(mentions),
            "unique_people": unique_people,
            "unique_people_count": len(unique_people),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "last_ts": last_ts,
            "by_person": [{"speaker": sp, "quotes": qs} for sp, qs in by_person.items()],
            # primary quote/speaker for compat
            "quote": mentions_sorted[0]["quote"] if mentions_sorted else "",
            "speaker": mentions_sorted[0]["speaker"] if mentions_sorted else "Unknown",
            "date": mentions_sorted[0]["date"] if mentions_sorted else "Unknown",
            "platform": mentions_sorted[0]["platform"] if mentions_sorted else "unknown",
            "source": mentions_sorted[0]["source"] if mentions_sorted else "Unknown",
            "ts": mentions_sorted[0]["ts"] if mentions_sorted else 0,
        })

    # Sort
    if sort_by == "recent":
        movie_cards.sort(key=lambda c: c["last_ts"], reverse=True)
    elif sort_by == "people":
        movie_cards.sort(key=lambda c: c["unique_people_count"], reverse=True)
    else:  # mentions (default)
        movie_cards.sort(key=lambda c: c["mention_count"], reverse=True)

    return movie_cards, quote_cards


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sort_by = request.args.get("sort", "mentions")
    if sort_by not in ("mentions", "recent", "people"):
        sort_by = "mentions"

    messages = fetch_movie_messages(400)
    movie_cards, quote_cards = process_messages(messages, sort_by=sort_by)

    all_platforms = sorted({c["platform"] for c in (movie_cards + quote_cards)})
    total_sources = len(set(
        (msg.get("source") or "").split(":")[0] for msg in messages
    ))
    total_mentions = sum(c["mention_count"] for c in movie_cards)

    return render_template(
        "index.html",
        movie_cards=movie_cards,
        quote_cards=quote_cards,
        total_messages=len(messages),
        total_movies=len(movie_cards),
        total_mentions=total_mentions,
        all_platforms=all_platforms,
        has_tmdb=bool(TMDB_API_KEY),
        total_sources=total_sources,
        db_path=DB_PATH,
        sort_by=sort_by,
    )


@app.route("/api/movies")
def api_movies():
    sort_by = request.args.get("sort", "mentions")
    messages = fetch_movie_messages(400)
    movie_cards, quote_cards = process_messages(messages, sort_by=sort_by)
    return jsonify({"movies": movie_cards, "quotes": quote_cards})


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
@app.route("/api/health")
def health():
    try:
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        return jsonify({"status": "ok", "memories": count, "tmdb": bool(TMDB_API_KEY)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("MOVIERING_PORT", "5050"))
    print(f"Movie Ring starting on http://0.0.0.0:{port}")
    print(f"  DB: {DB_PATH}")
    print(f"  TMDB: {'configured' if TMDB_API_KEY else 'NOT SET — movie posters disabled'}")
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("DEBUG")))
