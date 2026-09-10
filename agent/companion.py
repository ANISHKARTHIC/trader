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

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _analyze_one(
    symbol: str, is_holding: bool, analysis_date: str,
    account_equity: float, holdings_by_symbol: dict[str, Holding],
) -> tuple[ActionLabel, PipelineResult, HoldingEvaluation | None]:
    if is_holding:
        holding_eval = evaluate_holding(holdings_by_symbol[symbol], analysis_date)
        return _VERDICT_TO_LABEL[holding_eval.verdict], holding_eval.ta_result, holding_eval
    result = run_full_pipeline(f"{symbol}.NS", analysis_date, account_equity=account_equity)
    return _label_for_new_idea(result.trade_plan.side), result, None


def build_today(
    analysis_date: str | None = None,
    screen_top_n: int = 30,
    deep_analyze_top_n: int = 8,
    account_equity: float = 500_000.0,
    progress_cb=None,
    max_concurrent_analyses: int = 4,
) -> list[CompanionAction]:
    """Run the full companion pipeline. Blocking — call from a background job.

    `progress_cb(stage: str, done: int, total: int)` is called at each step
    so a caller (the web dashboard) can show live progress across what is,
    in total, potentially dozens of LLM-backed analyses.

    Each ticker's TradingAgents run is 15-20+ sequential LLM calls (see
    agent/pipeline.py) — that sequencing is intrinsic to the graph and not
    something this layer can shortcut. What this layer *can* fix is running
    several tickers' independent pipelines concurrently instead of one
    ticker fully finishing before the next starts, which is what made a
    full "Today" scan take hours instead of minutes. `max_concurrent_analyses`
    bounds how many tickers run at once — the Ollama cloud endpoint handles
    concurrent requests well (verified: 4 concurrent single-token calls
    completed in ~3s total, not 4x sequential), but going too wide risks
    hitting rate limits or genuinely saturating the model's own concurrency.
    TradingAgents' shared decision-log file is protected against the
    resulting concurrent writes by agent.pipeline._guard_memory_log_io.
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
    progress_lock = threading.Lock()
    done_count = 0

    def run_one(item: tuple[str, bool]) -> CompanionAction | None:
        nonlocal done_count
        symbol, is_holding = item
        try:
            label, result, holding_eval = _analyze_one(
                symbol, is_holding, analysis_date, account_equity, holdings_by_symbol
            )
        except Exception:
            return None  # one bad ticker (delisted, no data, LLM hiccup) shouldn't kill the run
        finally:
            with progress_lock:
                done_count += 1
                if progress_cb:
                    progress_cb("analyzing", done_count, total)
        return CompanionAction(
            symbol=symbol,
            label=label,
            is_existing_holding=is_holding,
            screen_score=screen_by_symbol[symbol].screen_score
            if symbol in screen_by_symbol
            else None,
            result=result,
            holding_eval=holding_eval,
        )

    if progress_cb:
        progress_cb("analyzing", 0, total)
    with ThreadPoolExecutor(max_workers=max(1, max_concurrent_analyses)) as pool:
        futures = [pool.submit(run_one, item) for item in to_analyze]
        for future in as_completed(futures):
            action = future.result()
            if action is not None:
                actions.append(action)

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
