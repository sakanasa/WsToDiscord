"""
LLM chat handler using a local Ollama instance.

Maintains per-channel conversation history so the model can follow context.
Supports web search via DuckDuckGo using Ollama tool calling — the model
decides autonomously when to search.
Call chat() from an async context via asyncio.to_thread().
"""
from __future__ import annotations

import logging
from collections import defaultdict

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:72b-instruct-q4_K_M"
MAX_HISTORY = 20      # messages to keep per channel (user + assistant combined)
MAX_TOOL_ROUNDS = 3   # prevent infinite tool-call loops

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

工具使用：
- 你有 web_search 工具可以使用
- 遇到時事、近況、不確定的資訊時主動搜尋，不要亂猜

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
                    "query": {
                        "type": "string",
                        "description": "搜尋關鍵字",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

# {channel_id: [{"role": "user"|"assistant", "content": "..."}]}
_history: dict[int, list[dict]] = defaultdict(list)


def _search_web(query: str) -> str:
    """Execute a DuckDuckGo search and return formatted results."""
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=3)
        if not results:
            return "搜尋無結果。"
        lines = []
        for r in results:
            lines.append(f"標題: {r['title']}\n摘要: {r['body']}\n連結: {r['href']}")
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning("llm: web search failed for %r: %s", query, e)
        return f"搜尋失敗：{e}"


def chat(channel_id: int, user_message: str, sender_name: str) -> str:
    """Send *user_message* to Ollama and return the assistant reply.

    The model may call web_search autonomously before answering.
    Conversation history is maintained per channel_id.
    Raises requests.RequestException on network/API failure.
    """
    history = _history[channel_id]
    labeled_message = f"[{sender_name}]: {user_message}"
    history.append({"role": "user", "content": labeled_message})

    # messages is the working list for this request (not persisted directly)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(history)
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
                # Model gave a final answer
                reply = msg.get("content", "")
                break

            # Execute each tool call and feed results back
            messages.append(msg)
            for tc in tool_calls:
                fn = tc["function"]
                if fn["name"] == "web_search":
                    query = fn["arguments"].get("query", "")
                    logger.info("llm: web_search %r", query)
                    result = _search_web(query)
                    messages.append({"role": "tool", "content": result})
        else:
            reply = "（搜尋次數已達上限，無法繼續）"

    except Exception:
        history.pop()  # don't save the failed user message
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
