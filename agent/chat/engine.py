"""The chat agent: a real tool-calling loop against the same free Ollama
cloud model used throughout this project (see agent.settings), grounded in
this app's own data via agent.chat.tools rather than the model's own
(possibly stale, definitely non-Indian-market-specific) training knowledge.

Not a simplified/rules-based router — the LLM decides which tools to call
based on the question, the same pattern TradingAgents itself uses for its
analyst tool-calling. Verified against the real Ollama endpoint: a tool
schema for get_quote produced a correct tool_calls response for "what is
the price of RELIANCE.NS" (see project history).

Escalation to a real Quick/Deep TradingAgents run is handled specially
(see maybe_start_analysis below) since it can't complete within one chat
turn — Quick mode alone takes ~2.5 minutes (verified: 153s for TCS).
"""

from __future__ import annotations

import json
import logging

import httpx

from agent.chat.tools import TOOL_IMPLS, TOOL_SCHEMAS
from agent.settings import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Verdict, a trading companion for NSE (Indian stock market) equities.

You have tools to look up the user's real portfolio, live quotes, price history, past AI trade calls and their outcomes, the user's own trading journal, and aggregate performance stats. Always call a tool to get real data rather than guessing or using memorized facts — prices, holdings, and past calls change and your training data is not a live source.

When the user gives a specific rupee amount and wants a fast same-day/intraday-style idea ("what can I buy with X rupees", "quick trade for X"), call get_budget_trade_idea with that symbol and amount — it answers in a couple of seconds with a real entry/stop/target/quantity sized to the budget. If they haven't named a symbol yet, ask which one first. Always pass along its caveat that this is based on the latest daily bar plus a live quote, not real intraday tick data (this app has no minute-level feed) — say that plainly, don't present it as precision intraday timing. If the tool's verdict is "avoid" or "too_expensive", say so directly rather than forcing a trade idea anyway.

When the user asks something that genuinely needs deep, multi-angle reasoning (e.g. "should I buy X for the long term", "is X a good long-term hold") rather than a quick factual lookup or a budget-sized idea, say so plainly and suggest running a full analysis — tell them you can start a Quick (about 2-3 minutes) or Deep (about 20-30 minutes) TradingAgents analysis on that symbol, and ask which they'd prefer, or start Quick by default if they just say "yes" or "analyze it".

Be direct and concise, like a knowledgeable colleague, not a disclaimer-laden chatbot. State uncertainty honestly — a rating or a lesson from a past decision is not a guarantee. Never claim a specific future price or invent a fact you have no tool for. This is not financial advice and you should say so only when the user is about to act on something significant, not on every message."""

MAX_TOOL_ROUNDS = 8  # a runaway tool-call loop should fail loudly, not hang a chat turn


def _ollama_chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    cfg = get_settings()
    resolved_model = cfg.quick_think_model  # the chat is a "quick" conversational role, not deep-think
    payload = {
        "model": resolved_model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    resp = httpx.post(
        f"{cfg.ollama_base_url}/chat/completions",
        json=payload,
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def _run_tool(name: str, arguments: dict) -> dict:
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"error": f"Unknown tool {name!r}"}
    try:
        return impl(**arguments)
    except Exception as exc:
        logger.warning("Chat tool %s failed: %s", name, exc, exc_info=True)
        return {"error": f"Tool {name} failed: {exc}"}


def run_chat_turn(history: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """One turn of conversation. `history` is prior [{role, content}] turns
    (tool-call turns are not persisted across calls — each turn resolves
    its own tool calls internally). Returns (assistant_reply_text,
    updated_history_to_store).

    Raises on a genuine failure (bad connection, model error) — the caller
    (webapp/server.py) is responsible for turning that into an HTTP error;
    a chat answer that silently swallowed a real failure would be worse
    than a visible one.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
        {"role": "user", "content": user_message}
    ]

    for _ in range(MAX_TOOL_ROUNDS):
        message = _ollama_chat(messages, tools=TOOL_SCHEMAS)
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            reply = message.get("content", "").strip()
            new_history = history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            ]
            return reply, new_history

        messages.append(message)
        for call in tool_calls:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_tool(fn["name"], args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result),
                }
            )

    # Hit MAX_TOOL_ROUNDS without a final answer — tell the user plainly
    # rather than silently returning nothing.
    fallback = (
        "I looked up several things but couldn't settle on an answer in time — "
        "try rephrasing, or ask about one thing at a time."
    )
    new_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": fallback},
    ]
    return fallback, new_history
