"""A fast, budget-sized trade idea for a specific rupee amount.

Deliberately not a TradingAgents run: the multi-agent debate takes 1-3+
minutes even in Quick mode (see agent.pipeline.ANALYSIS_MODES) because it
makes several real LLM calls. A "what can I buy with ~100 rupees right now"
question needs an answer in seconds, and doesn't need a debate — it needs
the same deterministic technical signals the screener already computes
(agent/screener/screen.py's _screen_one/_score), applied to one symbol,
sized against a fixed budget instead of a risk-percentage of total equity.

Explicit limitation, stated here and surfaced in every result: this project
has no live intraday (tick/minute-bar) data source. Every signal here is
computed from the most recent *daily* bar via yfinance — a same-day
directional lean grounded in yesterday's close and today's live quote, not
real intraday price action (no VWAP, no opening-range breakout, no minute
bars). Calling this "intraday" would overstate what it actually knows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import yfinance as yf
from stockstats import StockDataFrame

from agent.screener.screen import _score, _screen_one

# Score thresholds for a fast directional lean — looser than the screener's
# own ranking use (which only needs a relative order across 500 stocks);
# here the score needs to cross a real bar before recommending money.
BUY_SCORE_THRESHOLD = 8.0
SELL_SCORE_THRESHOLD = -8.0

ATR_STOP_MULTIPLE = 1.5  # tighter than the 2.0x used for swing trades in
# agent/translator/trade_plan.py — an intraday-labeled idea should carry a
# tighter leash, not the same stop distance as a multi-day swing position.
REWARD_RISK_RATIO = 1.5


@dataclass(frozen=True)
class BudgetTradeIdea:
    symbol: str
    verdict: str  # "buy", "sell", "avoid" (score too weak), "no_data", "too_expensive"
    reason: str
    last_price: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    quantity: int = 0
    cost: float = 0.0
    risk_amount: float = 0.0
    screen_score: float | None = None
    caveat: str = (
        "Based on the latest daily bar + live quote, not real intraday tick data — "
        "this project has no minute-level feed. Treat as a same-day directional "
        "lean, not a precision intraday signal."
    )


def get_budget_trade_idea(symbol: str, budget_rupees: float) -> BudgetTradeIdea:
    base = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    ns_symbol = f"{base}.NS"

    try:
        df = yf.Ticker(ns_symbol).history(period="1y")
    except Exception as exc:
        return BudgetTradeIdea(symbol=base, verdict="no_data", reason=f"Could not fetch data: {exc}")

    row = _screen_one(base, df)
    if row is None:
        return BudgetTradeIdea(
            symbol=base, verdict="no_data",
            reason="Not enough price history to compute a reliable signal for this symbol.",
        )

    score = _score(row)
    last_price = row["last_close"]

    # Live quote, if reachable, is a better "right now" price than the prior
    # close the daily-bar screen used — fall back to the daily close on any
    # fetch failure rather than blocking the whole idea on a live-quote hiccup.
    try:
        fast_info = yf.Ticker(ns_symbol).fast_info
        if fast_info.last_price:
            last_price = float(fast_info.last_price)
    except Exception:
        pass

    if last_price > budget_rupees:
        return BudgetTradeIdea(
            symbol=base, verdict="too_expensive", reason=(
                f"One share of {base} costs {_inr(last_price)}, more than your "
                f"{_inr(budget_rupees)} budget — can't size even 1 share."
            ),
            last_price=last_price, screen_score=score,
        )

    if score >= BUY_SCORE_THRESHOLD:
        side_sign = 1
        verdict = "buy"
    elif score <= SELL_SCORE_THRESHOLD:
        side_sign = -1
        verdict = "sell"
    else:
        return BudgetTradeIdea(
            symbol=base, verdict="avoid", reason=(
                f"{base}'s technical signals are mixed right now (score {score:+.1f}, "
                "no clear edge either way) — sitting this one out is the better call "
                "than forcing a trade on a small budget."
            ),
            last_price=last_price, screen_score=score,
        )

    # ATR isn't returned by _screen_one directly; recompute it the same way
    # the screener does (stockstats' atr column) rather than re-deriving a
    # different volatility estimate that could disagree with the score.
    sdf = StockDataFrame.retype(df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy())
    atr = float(sdf["atr"].iloc[-1])
    if atr <= 0 or math.isnan(atr):
        atr = last_price * 0.01  # degenerate/illiquid data — fall back to a 1% proxy rather than a zero stop

    stop_distance = ATR_STOP_MULTIPLE * atr
    entry = last_price
    stop = entry - side_sign * stop_distance
    target = entry + side_sign * REWARD_RISK_RATIO * stop_distance

    quantity = max(int(budget_rupees // entry), 0)
    if quantity == 0:
        return BudgetTradeIdea(
            symbol=base, verdict="too_expensive", reason=(
                f"{_inr(entry)} per share doesn't fit inside a {_inr(budget_rupees)} budget."
            ),
            last_price=last_price, screen_score=score,
        )

    cost = quantity * entry
    risk_amount = quantity * stop_distance

    reason = (
        f"{'Bullish' if verdict == 'buy' else 'Bearish'} technical read (score {score:+.1f}): "
        f"{_signal_summary(row)}."
    )

    return BudgetTradeIdea(
        symbol=base, verdict=verdict, reason=reason,
        last_price=last_price, entry=round(entry, 2), stop=round(stop, 2),
        target=round(target, 2), quantity=quantity, cost=round(cost, 2),
        risk_amount=round(risk_amount, 2), screen_score=score,
    )


def _signal_summary(row: dict) -> str:
    bits = []
    if row["momentum_21d"] > 0.02:
        bits.append(f"+{row['momentum_21d']*100:.1f}% over 21d")
    elif row["momentum_21d"] < -0.02:
        bits.append(f"{row['momentum_21d']*100:.1f}% over 21d")
    bits.append(f"RSI {row['rsi_14']:.0f}")
    if row["above_50sma"] and row["above_200sma"]:
        bits.append("above both SMAs")
    elif not row["above_50sma"] and not row["above_200sma"]:
        bits.append("below both SMAs")
    if row["near_52w_high"]:
        bits.append("near 52w high")
    if row["near_52w_low"]:
        bits.append("near 52w low")
    return ", ".join(bits)


def _inr(n: float) -> str:
    return f"₹{n:,.2f}"
