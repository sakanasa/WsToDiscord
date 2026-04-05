"""
Long-term memory system for 風庭的刀客.

Two storage layers:
  1. SQLite (data/user_profiles.db) — structured per-user facts extracted by LLM
  2. ChromaDB (data/chroma_db/)    — semantic vector index of past conversations

All Ollama calls use nomic-embed-text for embeddings.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent.parent / "data"

SQLITE_PATH = DATA_DIR / "user_profiles.db"
CHROMA_DIR = DATA_DIR / "chroma_db"

# ── Ollama embedding ─────────────────────────────────────────────────────────

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"


def embed_text(text: str) -> list[float]:
    """Return an embedding vector for *text* using Ollama nomic-embed-text."""
    resp = requests.post(
        OLLAMA_EMBED_URL,
        json={"model": EMBED_MODEL, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    # Ollama returns {"embeddings": [[...floats...]]}
    return data["embeddings"][0]


# ── ChromaDB custom embedding function ───────────────────────────────────────

class _OllamaEmbedFn:
    """chromadb EmbeddingFunction wrapper around embed_text()."""

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return [embed_text(t) for t in input]


# ── ChromaDB initialisation ──────────────────────────────────────────────────

_chroma_client = None
_chat_collection = None


def _get_collection():
    global _chroma_client, _chat_collection
    if _chat_collection is not None:
        return _chat_collection

    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _chat_collection = _chroma_client.get_or_create_collection(
        name="chat_history",
        embedding_function=_OllamaEmbedFn(),
        metadata={"hnsw:space": "cosine"},
    )
    logger.info("memory: ChromaDB collection ready (%d docs)", _chat_collection.count())
    return _chat_collection


# ── SQLite initialisation ─────────────────────────────────────────────────────

_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _db_conn = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False)
    _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id    TEXT PRIMARY KEY,
            name       TEXT,
            facts      TEXT DEFAULT '[]',
            updated_at TEXT
        )
    """)
    _db_conn.commit()
    logger.info("memory: SQLite ready at %s", SQLITE_PATH)
    return _db_conn


def init() -> None:
    """Initialise both storage backends. Call once at bot startup."""
    _get_db()
    try:
        _get_collection()
    except Exception as e:
        logger.warning("memory: ChromaDB init failed (will retry on first use): %s", e)


# ── User Profiling (SQLite) ──────────────────────────────────────────────────

def get_profile(user_id: str) -> dict[str, Any]:
    """Return the stored profile for *user_id*, or an empty dict."""
    row = _get_db().execute(
        "SELECT name, facts FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return {}
    name, facts_json = row
    return {"name": name, "facts": json.loads(facts_json)}


def update_profile(user_id: str, display_name: str, new_fact: str) -> None:
    """Append *new_fact* to the user's stored facts."""
    db = _get_db()
    row = db.execute(
        "SELECT facts FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()

    if row:
        facts: list[str] = json.loads(row[0])
    else:
        facts = []

    # Avoid exact duplicates
    if new_fact not in facts:
        facts.append(new_fact)
        # Keep at most 30 facts
        if len(facts) > 30:
            facts = facts[-30:]

    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    db.execute(
        """
        INSERT INTO user_profiles (user_id, name, facts, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            facts = excluded.facts,
            updated_at = excluded.updated_at
        """,
        (user_id, display_name, json.dumps(facts, ensure_ascii=False), now),
    )
    db.commit()
    logger.info("memory: saved fact for %s (%s): %s", display_name, user_id, new_fact)


def format_profile_for_prompt(user_id: str, display_name: str) -> str:
    """Return a formatted string for inclusion in the system prompt."""
    profile = get_profile(user_id)
    if not profile or not profile.get("facts"):
        return ""
    facts = profile["facts"]
    lines = "\n".join(f"  - {f}" for f in facts)
    return f"關於 {display_name} 你記得的事：\n{lines}"


# ── ChromaDB conversation store / retrieval ───────────────────────────────────

def store_conversation(
    channel_id: int,
    user_id: str,
    display_name: str,
    user_msg: str,
    assistant_msg: str,
) -> None:
    """Save a completed exchange to ChromaDB for future retrieval."""
    try:
        col = _get_collection()
        doc = f"[{display_name}]: {user_msg}\n[刀客]: {assistant_msg}"
        col.add(
            documents=[doc],
            metadatas=[{
                "channel_id": str(channel_id),
                "user_id": user_id,
                "display_name": display_name,
                "ts": str(time.time()),
            }],
            ids=[str(uuid.uuid4())],
        )
    except Exception as e:
        logger.warning("memory: failed to store conversation in ChromaDB: %s", e)


def search_history(query: str, channel_id: int, top_k: int = 3) -> list[str]:
    """Return the most semantically relevant past exchanges for *query*."""
    try:
        col = _get_collection()
        if col.count() == 0:
            return []
        results = col.query(
            query_texts=[query],
            n_results=min(top_k, col.count()),
            where={"channel_id": str(channel_id)},
        )
        docs = results.get("documents", [[]])[0]
        return docs
    except Exception as e:
        logger.warning("memory: ChromaDB search failed: %s", e)
        return []
