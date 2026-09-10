"""Surfaces TradingAgents' own decision-log reflections (already computed —
see vendor/TradingAgents/tradingagents/agents/utils/memory.py) as
structured data, joined with our own journal entries (agent/db/store.py).

Two distinct signals, kept distinct rather than merged into one score:
- TradingAgents' reflection: "was the rating right" — realized return vs.
  the NSEI/BSESN benchmark, and a one-paragraph lesson it writes itself.
- The user's journal: "what did I actually do" — followed/ignored/modified
  the call, and their own outcome notes.

This module does not duplicate TradingAgents' return-resolution logic (that
already runs inside TradingAgentsGraph.propagate() via
_resolve_pending_entries) — it only reads what's already been resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.db.store import list_journal_entries

_DEFAULT_LOG_PATH = Path.home() / ".tradingagents" / "memory" / "trading_memory.md"


@dataclass
class ReflectionEntry:
    date: str
    ticker: str
    rating: str
    pending: bool
    raw_return: str | None
    alpha_return: str | None
    holding_days: str | None
    resolved_date: str | None
    reflection: str


def _load_memory_log(config: dict | None = None) -> list[dict]:
    """Reuses TradingAgents' own TradingMemoryLog parser rather than
    re-implementing the markdown format here.
    """
    from tradingagents.agents.utils.memory import TradingMemoryLog

    log = TradingMemoryLog(config or {"memory_log_path": str(_DEFAULT_LOG_PATH)})
    return log.load_entries()


def get_reflections(
    symbol: str | None = None, resolved_only: bool = True, limit: int = 100
) -> list[ReflectionEntry]:
    """TradingAgents' own resolved-outcome reflections, newest first."""
    entries = _load_memory_log()
    out = []
    for e in entries:
        if resolved_only and e.get("pending"):
            continue
        if symbol and e["ticker"].removesuffix(".NS").removesuffix(".BO") != symbol.upper():
            continue
        out.append(
            ReflectionEntry(
                date=e["date"],
                ticker=e["ticker"],
                rating=e["rating"],
                pending=e["pending"],
                raw_return=e["raw"],
                alpha_return=e["alpha"],
                holding_days=e["holding"],
                resolved_date=e["resolved"],
                reflection=e["reflection"],
            )
        )
    out.sort(key=lambda r: r.resolved_date or r.date, reverse=True)
    return out[:limit]


def get_performance_summary() -> dict:
    """Aggregate stats across all resolved TradingAgents decisions: how often
    the rating direction matched the realized return, and by how much on
    average. A quick "is this thing actually any good" gut-check, not a
    substitute for a real backtest (see the research brief's Phase 15 on
    what a rigorous evaluation needs).
    """
    reflections = get_reflections(resolved_only=True, limit=10_000)
    if not reflections:
        return {"total_resolved": 0}

    def _pct_to_float(s: str | None) -> float | None:
        if not s:
            return None
        try:
            return float(s.strip().rstrip("%")) / 100.0
        except ValueError:
            return None

    correct = 0
    total_alpha = 0.0
    alpha_count = 0
    for r in reflections:
        raw = _pct_to_float(r.raw_return)
        alpha = _pct_to_float(r.alpha_return)
        if raw is not None:
            bullish = r.rating in ("Buy", "Overweight")
            bearish = r.rating in ("Sell", "Underweight")
            if (bullish and raw > 0) or (bearish and raw < 0) or (
                r.rating == "Hold" and abs(raw) < 0.02
            ):
                correct += 1
        if alpha is not None:
            total_alpha += alpha
            alpha_count += 1

    return {
        "total_resolved": len(reflections),
        "directionally_correct": correct,
        "directional_accuracy": round(correct / len(reflections), 3) if reflections else None,
        "avg_alpha_vs_benchmark": round(total_alpha / alpha_count, 4) if alpha_count else None,
    }


def get_journal_with_reflections(symbol: str | None = None, limit: int = 100) -> list[dict]:
    """Journal entries (what the user did) alongside the matching
    TradingAgents reflection for the same symbol/date, when one exists.
    """
    journal = list_journal_entries(symbol=symbol, limit=limit)
    reflections_by_key = {
        (r.ticker.removesuffix(".NS").removesuffix(".BO"), r.date): r
        for r in get_reflections(symbol=symbol, resolved_only=False, limit=10_000)
    }
    out = []
    for entry in journal:
        key = (entry["symbol"], entry["entry_date"])
        reflection = reflections_by_key.get(key)
        out.append(
            {
                **entry,
                "ai_reflection": reflection.reflection if reflection else None,
                "ai_rating": reflection.rating if reflection else None,
                "ai_raw_return": reflection.raw_return if reflection else None,
                "ai_alpha_return": reflection.alpha_return if reflection else None,
            }
        )
    return out
