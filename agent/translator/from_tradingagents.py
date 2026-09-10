"""Adapter: TradingAgents graph output -> TradePlanInputs.

Closes the loop between the two vendored/installed systems without touching
either: reads the *rendered markdown* TradingAgents already produces (the
graph's internal state only keeps rendered strings, not the raw Pydantic
PortfolioDecision/TraderProposal objects — see
vendor/TradingAgents/tradingagents/graph/trading_graph.py:559-574), reuses
TradingAgents' own `extract_rating` helper instead of re-implementing rating
parsing, and gets ATR from `stockstats` (already a TradingAgents dependency)
against the same OHLCV CSV TradingAgents itself cached.

No LLM call happens in this module. It is glue, not reasoning.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pandas as pd
from stockstats import StockDataFrame

from tradingagents.agents.utils.rating import extract_rating

from agent.translator.trade_plan import TradePlanInputs

CACHE_DIR = Path.home() / ".tradingagents" / "cache"

_PRICE_FIELD_RE = re.compile(
    r"\*\*Entry Price\*\*:\s*([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE
)
_STOP_FIELD_RE = re.compile(
    r"\*\*Stop Loss\*\*:\s*([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE
)


def _extract_price_field(pattern: re.Pattern, text: str) -> float | None:
    m = pattern.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _load_atr_and_last_price(symbol_ns: str, atr_period: int = 14) -> tuple[float, float]:
    """symbol_ns like 'RELIANCE.NS' -> (atr, last_close), from the cached CSV."""
    base = symbol_ns.removesuffix(".NS").removesuffix(".BO")
    matches = sorted(glob.glob(str(CACHE_DIR / f"{base}.NS-YFin-data-*.csv"))) or sorted(
        glob.glob(str(CACHE_DIR / f"{base}.BO-YFin-data-*.csv"))
    )
    if not matches:
        raise FileNotFoundError(
            f"No cached OHLCV CSV found for {symbol_ns} under {CACHE_DIR}. "
            "Run a TradingAgents analysis on this ticker first — it caches "
            "the data as a side effect."
        )
    df = pd.read_csv(matches[-1], parse_dates=["Date"]).sort_values("Date")
    sdf = StockDataFrame.retype(df.rename(columns=str.lower).copy())
    sdf["atr"]  # triggers computation with stockstats' default window
    atr = float(sdf["atr"].iloc[-1])
    last_close = float(df["Close"].iloc[-1])
    return atr, last_close


def build_trade_plan_inputs_from_state(
    final_state: dict,
    account_equity: float,
    risk_per_trade_pct: float = 0.5,
    atr_stop_multiple: float = 2.0,
    reward_risk_ratio: float = 2.0,
) -> TradePlanInputs:
    """Build TradePlanInputs from a completed TradingAgentsGraph.propagate() state.

    `final_state` is the dict returned as the first element of
    `TradingAgentsGraph.propagate(...)` (or `ta.curr_state` after a run).
    Prefers the Portfolio Manager's final_trade_decision rating; falls back to
    the Trader's proposal if the PM text doesn't parse (defensive — both are
    rendered by the schema's own render_* functions so this should be rare).
    """
    symbol = final_state["company_of_interest"]
    pm_text = final_state.get("final_trade_decision", "") or ""
    trader_text = final_state.get("trader_investment_plan", "") or ""

    rating = extract_rating(pm_text) or extract_rating(trader_text)
    if rating is None:
        raise ValueError(
            f"Could not extract a rating from TradingAgents output for {symbol}. "
            f"PM text: {pm_text[:200]!r}"
        )

    atr_14, last_price = _load_atr_and_last_price(symbol)

    return TradePlanInputs(
        symbol=symbol,
        rating=rating,
        last_price=last_price,
        atr_14=atr_14,
        account_equity=account_equity,
        risk_per_trade_pct=risk_per_trade_pct,
        atr_stop_multiple=atr_stop_multiple,
        reward_risk_ratio=reward_risk_ratio,
        llm_entry_price=_extract_price_field(_PRICE_FIELD_RE, trader_text),
        llm_stop_loss=_extract_price_field(_STOP_FIELD_RE, trader_text),
    )
