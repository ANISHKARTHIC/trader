"""The companion: turns screener output + current holdings into one ranked
"what to do today" action list, each item backed by a real TradingAgents
decision and a risk-sized trade plan — not just a raw rating.

Two sources of candidates feed the same deep-analysis step:
1. New opportunities: top-ranked symbols from the Stage-1 screen you don't
   already hold.
2. Existing holdings: every symbol you've entered in the portfolio store,
   re-analyzed so you get an explicit Add/Trim/Exit/Hold call instead of
   silence about what you already own.

Both paths run through the exact same run_full_pipeline() used by the
single-ticker dashboard — this module does not duplicate any analysis logic,
it only decides *which* tickers to run it on and how to label the result
for a holding vs. a fresh idea.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from agent.holding_eval import HoldingEvaluation, HoldingVerdict, evaluate_holding
from agent.pipeline import PipelineResult, run_full_pipeline
from agent.portfolio.store import Holding, list_holdings
from agent.screener.screen import ScreenResult, run_screen
from agent.translator.trade_plan import Side


class ActionLabel(str, Enum):
    BUY_NEW = "Buy (new position)"
    ADD = "Add to position"
    TRIM = "Trim position"
    EXIT = "Exit position"
    HOLD = "Hold"
    WATCH = "Watch (no action)"


_VERDICT_TO_LABEL = {
    HoldingVerdict.EXIT_STOP_HIT: ActionLabel.EXIT,
    HoldingVerdict.EXIT_TARGET_HIT: ActionLabel.EXIT,
    HoldingVerdict.EXIT_THESIS: ActionLabel.EXIT,
    HoldingVerdict.TRIM_THESIS: ActionLabel.TRIM,
    HoldingVerdict.ADD_THESIS: ActionLabel.ADD,
    HoldingVerdict.HOLD: ActionLabel.HOLD,
}


@dataclass
class CompanionAction:
    symbol: str
    label: ActionLabel
    is_existing_holding: bool
    screen_score: float | None
    result: PipelineResult
    holding_eval: HoldingEvaluation | None = None  # set only for existing holdings


def _label_for_new_idea(side: Side) -> ActionLabel:
    return ActionLabel.BUY_NEW if side is Side.BUY else ActionLabel.WATCH


def build_today(
    analysis_date: str | None = None,
    screen_top_n: int = 30,
    deep_analyze_top_n: int = 8,
    account_equity: float = 500_000.0,
    progress_cb=None,
) -> list[CompanionAction]:
    """Run the full companion pipeline. Blocking — call from a background job.

    `progress_cb(stage: str, done: int, total: int)` is called at each step
    so a caller (the web dashboard) can show live progress across what is,
    in total, potentially dozens of LLM-backed analyses.
    """
    analysis_date = analysis_date or date.today().isoformat()

    holdings = list_holdings()
    held_symbols = {h.symbol for h in holdings}
    holdings_by_symbol = {h.symbol: h for h in holdings}

    if progress_cb:
        progress_cb("screening", 0, 1)
    screen_results = run_screen(top_n=screen_top_n)
    screen_by_symbol = {r.symbol: r for r in screen_results}
    if progress_cb:
        progress_cb("screening", 1, 1)

    new_candidates = [r.symbol for r in screen_results if r.symbol not in held_symbols][
        :deep_analyze_top_n
    ]
    # Holdings first: an exit/trim call on money already at risk matters more
    # than a fresh idea, and the priority sort below reflects that too.
    to_analyze: list[tuple[str, bool]] = [(s, True) for s in held_symbols] + [
        (s, False) for s in new_candidates
    ]

    actions: list[CompanionAction] = []
    total = len(to_analyze)
    for i, (symbol, is_holding) in enumerate(to_analyze):
        if progress_cb:
            progress_cb("analyzing", i, total)
        try:
            if is_holding:
                holding_eval = evaluate_holding(holdings_by_symbol[symbol], analysis_date)
                result = holding_eval.ta_result
                label = _VERDICT_TO_LABEL[holding_eval.verdict]
            else:
                holding_eval = None
                result = run_full_pipeline(
                    f"{symbol}.NS", analysis_date, account_equity=account_equity
                )
                label = _label_for_new_idea(result.trade_plan.side)
        except Exception:
            continue  # one bad ticker (delisted, no data, LLM hiccup) shouldn't kill the run
        actions.append(
            CompanionAction(
                symbol=symbol,
                label=label,
                is_existing_holding=is_holding,
                screen_score=screen_by_symbol[symbol].screen_score
                if symbol in screen_by_symbol
                else None,
                result=result,
                holding_eval=holding_eval,
            )
        )
    if progress_cb:
        progress_cb("analyzing", total, total)

    _ACTION_PRIORITY = {
        ActionLabel.EXIT: 0,
        ActionLabel.TRIM: 1,
        ActionLabel.BUY_NEW: 2,
        ActionLabel.ADD: 3,
        ActionLabel.HOLD: 4,
        ActionLabel.WATCH: 5,
    }
    actions.sort(key=lambda a: (_ACTION_PRIORITY[a.label], -(a.screen_score or 0)))
    return actions
