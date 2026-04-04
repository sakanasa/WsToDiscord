"""
LLM chat handler using a local Ollama instance.

Maintains per-channel conversation history so the model can follow context.
Call chat() from an async context via asyncio.to_thread().
"""
from __future__ import annotations

import logging
from collections import defaultdict

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:72b"
MAX_HISTORY = 20  # messages to keep per channel (user + assistant combined)

# {channel_id: [{"role": "user"|"assistant", "content": "..."}]}
_history: dict[int, list[dict]] = defaultdict(list)


def chat(channel_id: int, user_message: str) -> str:
    """Send *user_message* to Ollama and return the assistant reply.

    Conversation history is maintained per channel_id.
    Raises requests.RequestException on network/API failure.
    """
    history = _history[channel_id]
    history.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": list(history), "stream": False},
            timeout=180,
        )
        resp.raise_for_status()
        reply = resp.json()["message"]["content"]
    except Exception:
        history.pop()  # don't save the failed message
        raise

    history.append({"role": "assistant", "content": reply})

    # Trim to keep memory bounded
    if len(history) > MAX_HISTORY:
        _history[channel_id] = history[-MAX_HISTORY:]

    return reply


def clear_history(channel_id: int) -> int:
    """Clear conversation history for a channel. Returns number of messages removed."""
    removed = len(_history.pop(channel_id, []))
    return removed
