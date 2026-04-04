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
OLLAMA_MODEL = "qwen2.5:72b-instruct-q4_K_M"
MAX_HISTORY = 20  # messages to keep per channel (user + assistant combined)

SYSTEM_PROMPT = """你叫做「風庭的刀客」，簡稱「刀客」。

個性與風格：
- 使用繁體中文回答
- 熟悉網路梗文化，講話有梗、接地氣
- 回答要精簡，不要長篇大論，能一句話解決就不說兩句
- 偶爾用台灣當兵梗調侃人（例如：收假、靠腰、長官、放假）
- 表面上毒蛇、嘴砲，但骨子裡熱心，是大家的好朋友
- 身在台灣，了解台灣文化與時事

記住：你是刀客，不是AI助理，不要說「我是AI語言模型」之類的話。"""

# {channel_id: [{"role": "user"|"assistant"|"system", "content": "..."}]}
_history: dict[int, list[dict]] = defaultdict(list)


def chat(channel_id: int, user_message: str) -> str:
    """Send *user_message* to Ollama and return the assistant reply.

    Conversation history is maintained per channel_id.
    Raises requests.RequestException on network/API failure.
    """
    history = _history[channel_id]
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(history)

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
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
