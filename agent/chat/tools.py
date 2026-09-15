"""Tools the chat agent can call: OpenAI-style function schemas plus their
Python implementations. Every tool wraps an existing, already-tested data
function from elsewhere in the project (agent.portfolio.store,
agent.db.store, agent.db.learning) — this module adds no new data logic of
its own except live-quote/history, which reuses yfinance, already a
project-wide dependency (see agent/screener/screen.py, agent/pipeline.py).

The one tool that isn't a pure data read is start_analysis: it starts a
real TradingAgents run (same pipeline as the Research tab) in the
background and returns immediately with a job id, since a chat turn can't
block for the 1-3 minutes even Quick mode takes. The frontend polls the
existing /api/jobs/{id} endpoint and reports back once it finishes.
"""

from __future__ import annotations

import yfinance as yf

from agent.db.learning import get_performance_summary, get_reflections
from agent.db.store import list_decisions_for_symbol, list_journal_entries, list_scans
from agent.intraday_idea import get_budget_trade_idea
from agent.portfolio.store import list_holdings

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Get the user's current stock holdings: symbol, quantity, average buy price.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "Get the current/last price and today's OHLCV for an NSE stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol without suffix, e.g. RELIANCE"}
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": "Get recent daily price history (close, % change) for an NSE stock, e.g. to describe a trend.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol without suffix, e.g. RELIANCE"},
                    "days": {"type": "integer", "description": "How many trading days back, default 20"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_budget_trade_idea",
            "description": (
                "Fast (a couple seconds), deterministic trade idea for a specific stock sized to an exact "
                "rupee budget — entry, stop, target, and share quantity that fits the budget. Use this "
                "instead of start_analysis whenever the user gives a specific rupee amount and wants a "
                "quick same-day/intraday-style answer rather than a full multi-minute TradingAgents debate. "
                "Based on the latest daily bar + live quote (no real intraday tick data exists in this "
                "app) — always pass this limitation on to the user, don't present it as precision intraday "
                "timing. If the user names a symbol, call this directly with it; if they don't name one, "
                "ask which symbol first rather than guessing one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol without suffix, e.g. RELIANCE"},
                    "budget_rupees": {"type": "number", "description": "Exact rupee amount available to spend"},
                },
                "required": ["symbol", "budget_rupees"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_past_decisions",
            "description": "Get this app's past TradingAgents ratings/trade plans for a specific symbol, most recent first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol without suffix, e.g. RELIANCE"}
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ai_reflections",
            "description": "Get TradingAgents' own resolved-outcome reflections (was a past rating right, and why) for a symbol, or across all symbols if none given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Optional NSE symbol to filter to"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_journal_entries",
            "description": "Get the user's own manual trading journal notes (what they actually did), optionally filtered to a symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Optional NSE symbol to filter to"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_performance_summary",
            "description": "Get aggregate stats on how accurate this app's past AI calls have been: directional accuracy, average alpha vs. the Nifty/Sensex benchmark.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_scans",
            "description": "List recent 'Today' companion scans (date, status) the user has run.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_analysis",
            "description": (
                "Start a real TradingAgents analysis on a symbol, after the user has confirmed "
                "they want one (they said yes/go ahead/quick/deep, not just asked a general "
                "question). Runs in the background — tell the user it's started and roughly how "
                "long it will take; you will not get the result in this same turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol without suffix, e.g. RELIANCE"},
                    "mode": {
                        "type": "string",
                        "enum": ["quick", "deep"],
                        "description": "quick (~2-3 min) unless the user specifically asked for deep",
                    },
                },
                "required": ["symbol", "mode"],
            },
        },
    },
]

# webapp/server.py registers its job-starting function here at import time,
# so agent/chat/ never imports from webapp/ (wrong dependency direction —
# agent/ is the library, webapp/ is one consumer of it). None until
# registered means the tool degrades to an explanatory error rather than
# crashing, e.g. if the chat engine is ever used outside the web app.
_start_analysis_callback = None


def register_start_analysis(callback) -> None:
    """callback(symbol: str, mode: str) -> dict with at least a 'job_id' key."""
    global _start_analysis_callback
    _start_analysis_callback = callback


def _to_ns(symbol: str) -> str:
    symbol = symbol.strip().upper()
    return symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"


def tool_get_portfolio(**_) -> dict:
    holdings = list_holdings()
    return {"holdings": [{"symbol": h.symbol, "quantity": h.quantity, "avg_price": h.avg_price} for h in holdings]}


def tool_get_quote(symbol: str, **_) -> dict:
    ns_symbol = _to_ns(symbol)
    try:
        info = yf.Ticker(ns_symbol).fast_info
        return {
            "symbol": ns_symbol,
            "last_price": info.last_price,
            "day_high": info.day_high,
            "day_low": info.day_low,
            "previous_close": info.previous_close,
            "volume": info.last_volume,
            "year_high": info.year_high,
            "year_low": info.year_low,
        }
    except Exception as exc:
        return {"error": f"Could not fetch a quote for {ns_symbol}: {exc}"}


def tool_get_price_history(symbol: str, days: int = 20, **_) -> dict:
    ns_symbol = _to_ns(symbol)
    try:
        df = yf.Ticker(ns_symbol).history(period=f"{max(days, 5) + 5}d")
        if df.empty:
            return {"error": f"No price history found for {ns_symbol}"}
        df = df.tail(days)
        closes = df["Close"].round(2).tolist()
        pct_change = round((closes[-1] / closes[0] - 1) * 100, 2) if closes[0] else None
        return {
            "symbol": ns_symbol,
            "closes": closes,
            "pct_change_over_period": pct_change,
            "trading_days": len(closes),
        }
    except Exception as exc:
        return {"error": f"Could not fetch price history for {ns_symbol}: {exc}"}


def tool_get_budget_trade_idea(symbol: str, budget_rupees: float, **_) -> dict:
    idea = get_budget_trade_idea(symbol, budget_rupees)
    return {
        "symbol": idea.symbol,
        "verdict": idea.verdict,
        "reason": idea.reason,
        "last_price": idea.last_price,
        "entry": idea.entry,
        "stop": idea.stop,
        "target": idea.target,
        "quantity": idea.quantity,
        "cost": idea.cost,
        "risk_amount": idea.risk_amount,
        "screen_score": idea.screen_score,
        "caveat": idea.caveat,
    }


def tool_get_past_decisions(symbol: str, **_) -> dict:
    base = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    decisions = list_decisions_for_symbol(base, limit=10)
    return {
        "decisions": [
            {
                "analysis_date": d["analysis_date"],
                "action_label": d["action_label"],
                "rating": d["rating"],
                "side": d["side"],
            }
            for d in decisions
        ]
    }


def tool_get_ai_reflections(symbol: str | None = None, **_) -> dict:
    reflections = get_reflections(symbol=symbol, resolved_only=True, limit=10)
    return {
        "reflections": [
            {
                "ticker": r.ticker, "rating": r.rating, "raw_return": r.raw_return,
                "alpha_return": r.alpha_return, "reflection": r.reflection,
            }
            for r in reflections
        ]
    }


def tool_get_journal_entries(symbol: str | None = None, **_) -> dict:
    entries = list_journal_entries(symbol=symbol, limit=15)
    return {
        "entries": [
            {
                "symbol": e["symbol"], "entry_date": e["entry_date"],
                "action_taken": e["action_taken"], "notes": e["notes"],
            }
            for e in entries
        ]
    }


def tool_get_performance_summary(**_) -> dict:
    return get_performance_summary()


def tool_get_recent_scans(**_) -> dict:
    scans = list_scans(limit=10)
    return {
        "scans": [
            {"analysis_date": s["analysis_date"], "kind": s["kind"], "status": s["status"]}
            for s in scans
        ]
    }


def tool_start_analysis(symbol: str, mode: str = "quick", **_) -> dict:
    if _start_analysis_callback is None:
        return {"error": "Analysis cannot be started from this context — no callback registered."}
    if mode not in ("quick", "deep"):
        mode = "quick"
    try:
        return _start_analysis_callback(_to_ns(symbol), mode)
    except Exception as exc:
        return {"error": f"Could not start analysis: {exc}"}


TOOL_IMPLS = {
    "get_portfolio": tool_get_portfolio,
    "get_quote": tool_get_quote,
    "get_price_history": tool_get_price_history,
    "get_budget_trade_idea": tool_get_budget_trade_idea,
    "get_past_decisions": tool_get_past_decisions,
    "get_ai_reflections": tool_get_ai_reflections,
    "get_journal_entries": tool_get_journal_entries,
    "get_performance_summary": tool_get_performance_summary,
    "get_recent_scans": tool_get_recent_scans,
    "start_analysis": tool_start_analysis,
}
