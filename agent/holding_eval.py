"""Sell/exit evaluation for an existing holding.

A fresh opportunity and an existing position are not the same question. For
a new idea, "what should the entry/stop/target be" is the right question
(agent/translator/trade_plan.py, entry defaults to the current price). For a
position you already hold, the right questions are: what did I pay, what's
my unrealized P&L, has a deterministic stop already been breached, and does
today's re-analysis still support holding.

Two independent signals feed the final call, deliberately not merged into
one score:
1. Hard stop/target — deterministic, computed once from the entry price and
   ATR at time of evaluation, checked directly against the current price.
   This is the safety net: it doesn't wait for or depend on the LLM.
2. Thesis re-check — today's TradingAgents rating on the same symbol. This
   can say Sell/Underweight even when price hasn't hit any hard level, e.g.
   because the fundamental or technical picture changed.

Either one alone can trigger EXIT. This mirrors the "risk engine can veto
the AI, and a hard rule can override a debate" principle used throughout
this project — the LLM doesn't get to be the only thing standing between a
bad position and an exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.pipeline import PipelineResult, run_paper_trade, run_trading_agents
from agent.portfolio.store import Holding
from agent.translator.from_tradingagents import load_atr_and_last_price


class HoldingVerdict(str, Enum):
    EXIT_STOP_HIT = "Exit — stop-loss breached"
    EXIT_TARGET_HIT = "Exit — target reached"
    EXIT_THESIS = "Exit — thesis turned negative"
    TRIM_THESIS = "Trim — thesis weakening"
    ADD_THESIS = "Add — thesis strengthening"
    HOLD = "Hold"


@dataclass
class HoldingEvaluation:
    holding: Holding
    last_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    hard_stop: float
    hard_target: float
    verdict: HoldingVerdict
    ta_result: PipelineResult  # full TradingAgents re-analysis, same as any other run


def _hard_levels(entry_price: float, atr_14: float, atr_stop_multiple: float = 2.0,
                  reward_risk_ratio: float = 2.0) -> tuple[float, float]:
    """Stop/target computed from the position's actual entry price, not a
    fresh signal price — this is what "protect what I already bought" means.
    """
    stop_distance = atr_stop_multiple * atr_14
    stop = entry_price - stop_distance
    target = entry_price + reward_risk_ratio * stop_distance
    return stop, target


def evaluate_holding(
    holding: Holding,
    analysis_date: str,
    llm_provider: str | None = None,
    deep_think_llm: str | None = None,
    quick_think_llm: str | None = None,
    mode: str = "deep",
) -> HoldingEvaluation:
    """Run the hard-stop check and a TradingAgents re-analysis on one holding.

    mode: "quick" or "deep" — see agent.pipeline.ANALYSIS_MODES. The hard
    stop/target check below is unaffected by mode; it's pure price math,
    never an LLM call.
    """
    from agent.pipeline import ANALYSIS_MODES

    symbol_ns = holding.yfinance_symbol
    preset = ANALYSIS_MODES[mode]

    final_state = run_trading_agents(
        symbol_ns,
        analysis_date,
        llm_provider=llm_provider,
        deep_think_llm=deep_think_llm,
        quick_think_llm=quick_think_llm,
        selected_analysts=preset["selected_analysts"],
        max_debate_rounds=preset["max_debate_rounds"],
        max_risk_discuss_rounds=preset["max_risk_discuss_rounds"],
    )

    ta_result = run_paper_trade(
        final_state, account_equity=holding.quantity * holding.avg_price, mode=mode
    )

    atr_14, last_price = load_atr_and_last_price(symbol_ns)
    stop, target = _hard_levels(holding.avg_price, atr_14)

    unrealized_pnl = (last_price - holding.avg_price) * holding.quantity
    unrealized_pnl_pct = (last_price / holding.avg_price - 1) * 100 if holding.avg_price else 0.0

    rating = ta_result.trade_plan.source_rating

    if last_price <= stop:
        verdict = HoldingVerdict.EXIT_STOP_HIT
    elif last_price >= target:
        verdict = HoldingVerdict.EXIT_TARGET_HIT
    elif rating == "Sell":
        verdict = HoldingVerdict.EXIT_THESIS
    elif rating == "Underweight":
        verdict = HoldingVerdict.TRIM_THESIS
    elif rating in ("Buy", "Overweight"):
        verdict = HoldingVerdict.ADD_THESIS
    else:
        verdict = HoldingVerdict.HOLD

    return HoldingEvaluation(
        holding=holding,
        last_price=last_price,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=unrealized_pnl_pct,
        hard_stop=stop,
        hard_target=target,
        verdict=verdict,
        ta_result=ta_result,
    )
