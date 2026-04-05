"""
LLM chat handler using a local Ollama instance.

Memory architecture:
  - Short-term : sliding window per channel (in-memory, MAX_HISTORY messages)
  - User profile: SQLite facts extracted by LLM via save_memory tool
  - Long-term   : ChromaDB semantic search over past conversations

Call chat() from an async context via asyncio.to_thread().
"""
from __future__ import annotations

import logging
from collections import defaultdict

import requests

from commands import memory as mem

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:72b-instruct-q4_K_M"
MAX_HISTORY = 20      # short-term messages per channel
MAX_TOOL_ROUNDS = 4   # max LLM→tool→LLM iterations per reply

SYSTEM_PROMPT = """你叫做「風庭的刀客」，簡稱「刀客」。

個性與風格：
- 如果要使用中文，一定要使用繁體中文回答
- 熟悉網路梗文化
- 回答要精簡，不要長篇大論，能一句話解決就不說兩句
- 偶爾用台灣當兵梗調侃人（例如：收假、靠腰、長官、放假）
- 骨子熱心，是大家的好朋友
- 身在台灣，了解台灣文化與時事
- 記住每個人說過的話，針對不同人給出不同反應
- 使用者「風庭」是這個群的群主，也是你的雇主

訊息格式說明：
- 每則訊息的格式是「[名字]: 內容」，名字是傳訊息的人的暱稱
- 你可以叫他們的名字，讓對話更自然
- 如果你想 tag 某人，直接寫 @名字 即可（例如：@小明）

記憶工具說明：
- 用 save_memory 主動記住關於群友的重要資訊（興趣、職業、事件等）
- 用 web_search 查詢時事或不確定的資訊

記住：你是刀客，不是AI助理，不要說「我是AI語言模型」之類的話。"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜尋網路上的最新資訊、時事新聞、或任何不確定的事實",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜尋關鍵字"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "永久記住關於某位群友的重要資訊，例如興趣、職業、喜好、重要事件",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "群友的 Discord user_id"},
                    "display_name": {"type": "string", "description": "群友的暱稱"},
                    "fact": {"type": "string", "description": "要記住的資訊，一句話描述"},
                },
                "required": ["user_id", "display_name", "fact"],
            },
        },
    },
]

# {channel_id: [{"role": "user"|"assistant", "content": "..."}]}
_history: dict[int, list[dict]] = defaultdict(list)


def _search_web(query: str) -> str:
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=3)
        if not results:
            return "搜尋無結果。"
        lines = [f"標題: {r['title']}\n摘要: {r['body']}\n連結: {r['href']}" for r in results]
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning("llm: web search failed for %r: %s", query, e)
        return f"搜尋失敗：{e}"


def _build_system_prompt(user_id: str, display_name: str, channel_id: int, user_msg: str) -> str:
    """Compose full system prompt with user profile and relevant history."""
    parts = [SYSTEM_PROMPT]

    # User profile
    profile_text = mem.format_profile_for_prompt(user_id, display_name)
    if profile_text:
        parts.append("\n\n--- 長期記憶（群友資料）---\n" + profile_text)

    # Relevant history from ChromaDB
    history_chunks = mem.search_history(user_msg, channel_id, top_k=3)
    if history_chunks:
        joined = "\n---\n".join(history_chunks)
        parts.append("\n\n--- 相關歷史對話（語義檢索）---\n" + joined)

    return "".join(parts)


def chat(
    channel_id: int,
    user_message: str,
    sender_name: str,
    user_id: str,
) -> str:
    """Send *user_message* to Ollama with full memory context and return the reply.

    Raises requests.RequestException on network/API failure.
    """
    history = _history[channel_id]
    labeled_message = f"[{sender_name}]: {user_message}"
    history.append({"role": "user", "content": labeled_message})

    system_prompt = _build_system_prompt(user_id, sender_name, channel_id, user_message)
    messages = [{"role": "system", "content": system_prompt}] + list(history)
    reply = ""

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "tools": _TOOLS,
                    "stream": False,
                },
                timeout=180,
            )
            resp.raise_for_status()
            msg = resp.json()["message"]

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                reply = msg.get("content", "")
                break

            messages.append(msg)
            for tc in tool_calls:
                fn = tc["function"]
                fn_name = fn["name"]
                args = fn.get("arguments", {})

                if fn_name == "web_search":
                    query = args.get("query", "")
                    logger.info("llm: web_search %r", query)
                    result = _search_web(query)

                elif fn_name == "save_memory":
                    uid = args.get("user_id", user_id)
                    dname = args.get("display_name", sender_name)
                    fact = args.get("fact", "")
                    if fact:
                        mem.update_profile(uid, dname, fact)
                    result = f"已記住：{fact}"

                else:
                    result = f"未知工具：{fn_name}"

                messages.append({"role": "tool", "content": result})
        else:
            reply = "（工具呼叫次數已達上限）"

    except Exception:
        history.pop()
        raise

    history.append({"role": "assistant", "content": reply})

    if len(history) > MAX_HISTORY:
        _history[channel_id] = history[-MAX_HISTORY:]

    # Persist conversation to ChromaDB (non-blocking best-effort)
    try:
        mem.store_conversation(channel_id, user_id, sender_name, user_message, reply)
    except Exception as e:
        logger.warning("llm: failed to store conversation: %s", e)

    return reply


def clear_history(channel_id: int) -> int:
    """Clear short-term (in-memory) history for a channel.
    Long-term memory (SQLite + ChromaDB) is preserved.
    """
    removed = len(_history.pop(channel_id, []))
    return removed
