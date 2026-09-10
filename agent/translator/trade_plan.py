"""Turns a TradingAgents decision into a sizeable, risk-checkable trade plan.

TradingAgents (vendor/TradingAgents) already produces two typed outputs that
matter here — see tradingagents/agents/schemas.py:

- TraderProposal: action (Buy/Hold/Sell), optional entry_price, optional
  stop_loss, free-text position_sizing guidance.
- PortfolioDecision: final 5-tier rating, optional price_target, optional
  time_horizon. No stop-loss field.

Neither gives a share quantity or a risk-checked stop, and an LLM should not
be trusted to do that arithmetic. This module is the deterministic bridge:
given the LLM's directional call plus a few numeric market facts, it computes
a concrete, auditable TradePlan. It never calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"  # Hold / Underweight-with-no-existing-position / no trade


# 5-tier PortfolioRating -> directional side. Overweight/Underweight without
# portfolio context reduce to the same action as Buy/Sell for a fresh entry;
# the risk gate downstream is responsible for turning "Underweight" into a
# trim instead of a fresh short when a position already exists.
_RATING_TO_SIDE = {
    "Buy": Side.BUY,
    "Overweight": Side.BUY,
    "Hold": Side.FLAT,
    "Underweight": Side.SELL,
    "Sell": Side.SELL,
}


@dataclass(frozen=True)
class TradePlanInputs:
    """Everything the translator needs, all deterministic/numeric — no prose."""

    symbol: str
    rating: str  # PortfolioRating.value, e.g. "Buy"
    last_price: float
    atr_14: float  # 14-period Average True Range, computed by your data layer
    account_equity: float
    risk_per_trade_pct: float = 0.5  # % of equity risked on this one trade
    atr_stop_multiple: float = 2.0  # stop = entry -/+ k * ATR
    reward_risk_ratio: float = 2.0  # target distance = R * stop distance
    llm_entry_price: float | None = None  # from TraderProposal.entry_price, if present
    llm_stop_loss: float | None = None  # from TraderProposal.stop_loss, if present


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    side: Side
    entry: float
    stop: float
    target: float
    risk_amount: float  # currency at risk if stop is hit
    suggested_qty: int
    stop_distance: float
    source_rating: str


def build_trade_plan(inputs: TradePlanInputs) -> TradePlan:
    """Deterministically derive an entry/stop/target/quantity from a rating.

    Design choices, stated so they're auditable:
    - The LLM's own entry/stop (when present) are used only as a sanity-checked
      starting point, never taken on faith: entry defaults to last_price
      (the market, not a hoped-for level), and the stop is always recomputed
      from ATR unless the LLM's stop is *tighter* (safer) than the ATR stop.
    - Position size is fixed-fractional risk sizing:
        suggested_qty = (equity * risk_per_trade_pct%) / stop_distance
      This is the standard, well-understood retail risk-sizing method (see
      Phase 9 of the research brief) — not a Kelly/optimal-f calculation,
      which is far more sensitive to input error for a single-strategy retail
      system.
    """
    side = _RATING_TO_SIDE[inputs.rating]
    if side is Side.FLAT:
        return TradePlan(
            symbol=inputs.symbol,
            side=side,
            entry=inputs.last_price,
            stop=inputs.last_price,
            target=inputs.last_price,
            risk_amount=0.0,
            suggested_qty=0,
            stop_distance=0.0,
            source_rating=inputs.rating,
        )

    entry = inputs.llm_entry_price or inputs.last_price
    atr_stop_distance = inputs.atr_stop_multiple * inputs.atr_14

    if side is Side.BUY:
        atr_stop = entry - atr_stop_distance
        stop = max(atr_stop, inputs.llm_stop_loss) if inputs.llm_stop_loss else atr_stop
        stop_distance = entry - stop
        target = entry + inputs.reward_risk_ratio * stop_distance
    else:  # SELL
        atr_stop = entry + atr_stop_distance
        stop = min(atr_stop, inputs.llm_stop_loss) if inputs.llm_stop_loss else atr_stop
        stop_distance = stop - entry
        target = entry - inputs.reward_risk_ratio * stop_distance

    if stop_distance <= 0:
        # Degenerate input (e.g. bad LLM stop straddling entry) — refuse to
        # size a trade rather than divide by zero or risk an unbounded amount.
        return TradePlan(
            symbol=inputs.symbol,
            side=Side.FLAT,
            entry=entry,
            stop=entry,
            target=entry,
            risk_amount=0.0,
            suggested_qty=0,
            stop_distance=0.0,
            source_rating=inputs.rating,
        )

    risk_amount = inputs.account_equity * (inputs.risk_per_trade_pct / 100.0)
    suggested_qty = int(risk_amount // stop_distance)

    return TradePlan(
        symbol=inputs.symbol,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        risk_amount=risk_amount,
        suggested_qty=suggested_qty,
        stop_distance=stop_distance,
        source_rating=inputs.rating,
    )
