"""The single entry point that runs the whole chain: TradingAgents decision
-> TradePlan -> NautilusTrader paper execution.

Extracted from agent/execution/run_paper_backtest.py so the web API and any
future CLI/script share one implementation instead of copy-pasted logic.
"""

from __future__ import annotations

import glob
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    OmsType,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity

from agent.execution.paper_strategy import TradePlanStrategy, TradePlanStrategyConfig
from agent.translator.trade_plan import TradePlan, build_trade_plan
from agent.translator.from_tradingagents import build_trade_plan_inputs_from_state

CACHE_DIR = Path.home() / ".tradingagents" / "cache"
VENUE = Venue("NSE")
PRICE_PRECISION = 2
LOT_SIZE = 1

# TradingAgents' TradingMemoryLog does a read -> rewrite -> atomic-replace on
# a single shared file (batch_update_with_outcomes / store_decision's
# idempotency read) — safe for one propagate() call at a time, but two
# tickers' pipelines running concurrently (see agent/companion.py's thread
# pool) can race on it: the second writer's read predates the first
# writer's update, so it silently clobbers it. The 15-20 sequential LLM
# calls inside a single propagate() run touch no shared file at all, only
# its narrow start/end memory-log I/O does — so a lock scoped to just that
# I/O (not the whole call) preserves almost all of the parallelism gain.
# See _locked_memory_log below for where this is applied.
_memory_log_io_lock = threading.Lock()


@dataclass
class PipelineResult:
    symbol: str
    analysis_date: str
    pm_decision_markdown: str
    trader_proposal_markdown: str
    market_report: str
    fundamentals_report: str
    trade_plan: TradePlan
    order_filled: bool
    account_report: str
    fills_report: str
    positions_report: str
    log_lines: list[str] = field(default_factory=list)


def _find_cached_csv(symbol_base: str) -> Path:
    matches = sorted(glob.glob(str(CACHE_DIR / f"{symbol_base}.NS-YFin-data-*.csv")))
    if not matches:
        raise FileNotFoundError(
            f"No cached OHLCV CSV found for {symbol_base}.NS under {CACHE_DIR}. "
            "The TradingAgents run above should have cached it as a side effect."
        )
    return Path(matches[-1])


def _load_bars(csv_path: Path, bar_type: BarType) -> list[Bar]:
    df = pd.read_csv(csv_path, parse_dates=["Date"]).sort_values("Date")
    bars = []
    for row in df.itertuples(index=False):
        ts_ns = int(pd.Timestamp(row.Date).tz_localize("Asia/Kolkata").value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(round(row.Open, PRICE_PRECISION), precision=PRICE_PRECISION),
                high=Price(round(row.High, PRICE_PRECISION), precision=PRICE_PRECISION),
                low=Price(round(row.Low, PRICE_PRECISION), precision=PRICE_PRECISION),
                close=Price(round(row.Close, PRICE_PRECISION), precision=PRICE_PRECISION),
                volume=Quantity(int(row.Volume), precision=0),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
        )
    return bars


def run_trading_agents(
    symbol_ns: str,
    analysis_date: str,
    llm_provider: str = "ollama",
    deep_think_llm: str = "gpt-oss:120b-cloud",
    quick_think_llm: str = "gpt-oss:120b-cloud",
    # Full analyst roster: market (technical), social (sentiment — news +
    # StockTwits + Reddit), news (macro/company news), fundamentals. This is
    # every analyst TradingAgents ships, not a subset — see
    # vendor/TradingAgents/tradingagents/graph/analyst_execution.py, where
    # "social" is the sentiment analyst's wire-key.
    selected_analysts: tuple[str, ...] = ("market", "social", "news", "fundamentals"),
    max_debate_rounds: int = 2,
    max_risk_discuss_rounds: int = 2,
) -> dict:
    """Run TradingAgents for real and return its final state dict."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = llm_provider
    config["deep_think_llm"] = deep_think_llm
    config["quick_think_llm"] = quick_think_llm
    config["max_debate_rounds"] = max_debate_rounds
    config["max_risk_discuss_rounds"] = max_risk_discuss_rounds

    ta = TradingAgentsGraph(
        selected_analysts=selected_analysts,
        debug=False,
        config=config,
    )
    _guard_memory_log_io(ta.memory_log)
    final_state, _signal = ta.propagate(symbol_ns, analysis_date)
    return final_state


def _guard_memory_log_io(memory_log) -> None:
    """Wrap a TradingMemoryLog instance's file-touching methods so concurrent
    propagate() calls (agent/companion.py runs several tickers' pipelines in
    parallel threads) can't race on its shared log file. See the module-level
    comment above _memory_log_io_lock for why this is needed and why it's
    scoped this narrowly rather than locking the whole pipeline call.
    """
    for method_name in ("store_decision", "batch_update_with_outcomes"):
        original = getattr(memory_log, method_name)

        def locked(*args, _original=original, **kwargs):
            with _memory_log_io_lock:
                return _original(*args, **kwargs)

        setattr(memory_log, method_name, locked)


def run_paper_trade(final_state: dict, account_equity: float = 500_000.0) -> PipelineResult:
    """Take a completed TradingAgents state and paper-trade the resulting plan."""
    symbol_ns = final_state["company_of_interest"]
    symbol_base = symbol_ns.removesuffix(".NS").removesuffix(".BO")
    analysis_date = final_state["trade_date"]

    plan_inputs = build_trade_plan_inputs_from_state(
        final_state, account_equity=account_equity, risk_per_trade_pct=0.5
    )
    plan = build_trade_plan(plan_inputs)

    csv_path = _find_cached_csv(symbol_base)
    instrument_id = InstrumentId(Symbol(symbol_base), VENUE)
    bar_type = BarType(
        instrument_id=instrument_id,
        bar_spec=BarSpecification(1, BarAggregation.DAY, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
    bars = _load_bars(csv_path, bar_type)

    equity = Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol_base),
        currency=INR,
        price_precision=PRICE_PRECISION,
        price_increment=Price(0.05, precision=PRICE_PRECISION),
        lot_size=Quantity(LOT_SIZE, precision=0),
        ts_event=bars[0].ts_event,
        ts_init=bars[0].ts_event,
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("PAPER-001"),
            logging=LoggingConfig(log_level="WARNING"),
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=INR,
        starting_balances=[Money(account_equity, INR)],
    )
    engine.add_instrument(equity)
    engine.add_data(bars)

    strategy = TradePlanStrategy(
        TradePlanStrategyConfig(
            instrument_id=instrument_id,
            price_precision=PRICE_PRECISION,
            size_precision=0,
        )
    )
    strategy.set_trade_plan(plan)
    engine.add_strategy(strategy)

    engine.run()

    account_report = str(engine.trader.generate_account_report(VENUE))
    fills_report = str(engine.trader.generate_order_fills_report())
    positions_report = str(engine.trader.generate_positions_report())

    engine.reset()
    engine.dispose()

    return PipelineResult(
        symbol=symbol_ns,
        analysis_date=analysis_date,
        pm_decision_markdown=final_state.get("final_trade_decision", ""),
        trader_proposal_markdown=final_state.get("trader_investment_plan", ""),
        market_report=final_state.get("market_report", ""),
        fundamentals_report=final_state.get("fundamentals_report", ""),
        trade_plan=plan,
        order_filled="Filled" in fills_report or "FILLED" in fills_report,
        account_report=account_report,
        fills_report=fills_report,
        positions_report=positions_report,
    )


def run_full_pipeline(
    symbol_ns: str,
    analysis_date: str,
    account_equity: float = 500_000.0,
    llm_provider: str = "ollama",
    deep_think_llm: str = "gpt-oss:120b-cloud",
    quick_think_llm: str = "gpt-oss:120b-cloud",
) -> PipelineResult:
    """Run TradingAgents, then paper-trade the resulting decision. Blocking."""
    final_state = run_trading_agents(
        symbol_ns,
        analysis_date,
        llm_provider=llm_provider,
        deep_think_llm=deep_think_llm,
        quick_think_llm=quick_think_llm,
    )
    return run_paper_trade(final_state, account_equity=account_equity)
