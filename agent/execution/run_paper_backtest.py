"""End-to-end: run TradingAgents on a ticker, translate its real decision into
a TradePlan, and submit it as a NautilusTrader paper-trading bracket order
with real risk gating.

This is a smoke test proving the full chain works, not a strategy backtest
in the proper sense (see Phase 14 of the research brief for what a real
walk-forward backtest needs — this script trades on exactly one decision,
made with knowledge only up to `ANALYSIS_DATE`, then executed against the
next available bar).

Requires a local/cloud Ollama model (see vendor/TradingAgents README) — no
paid LLM API key needed if using an Ollama cloud model such as
gpt-oss:120b-cloud.

Run:
    python -m agent.execution.run_paper_backtest
"""

from __future__ import annotations

import glob
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
from agent.translator.trade_plan import build_trade_plan
from agent.translator.from_tradingagents import build_trade_plan_inputs_from_state

CACHE_DIR = Path.home() / ".tradingagents" / "cache"
VENUE = Venue("NSE")
SYMBOL = "RELIANCE"
ANALYSIS_DATE = "2026-08-15"
ACCOUNT_EQUITY = 500_000.0
PRICE_PRECISION = 2
LOT_SIZE = 1  # NSE cash equity trades in single shares, no board lot for most stocks


def _find_cached_csv(symbol: str) -> Path:
    matches = sorted(glob.glob(str(CACHE_DIR / f"{symbol}.NS-YFin-data-*.csv")))
    if not matches:
        raise FileNotFoundError(
            f"No cached OHLCV CSV found for {symbol}.NS under {CACHE_DIR}. "
            "Run a TradingAgents analysis on this ticker first."
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


def _get_trading_agents_decision(symbol_ns: str, analysis_date: str) -> dict:
    """Run TradingAgents for real and return its final state dict.

    Uses the same Ollama-cloud setup verified earlier in this project (no
    paid API key). Two analysts and one debate round keep call count down —
    this is a plumbing demo, not a tuned research configuration.
    """
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "ollama"
    config["deep_think_llm"] = "gpt-oss:120b-cloud"
    config["quick_think_llm"] = "gpt-oss:120b-cloud"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1

    ta = TradingAgentsGraph(
        selected_analysts=("market", "fundamentals"),
        debug=False,
        config=config,
    )
    final_state, _signal = ta.propagate(symbol_ns, analysis_date)
    return final_state


def main() -> None:
    ta_symbol = f"{SYMBOL}.NS"
    print(f"Running TradingAgents on {ta_symbol} for {ANALYSIS_DATE} ...")
    final_state = _get_trading_agents_decision(ta_symbol, ANALYSIS_DATE)
    print(f"PM decision:\n{final_state['final_trade_decision']}\n")

    plan_inputs = build_trade_plan_inputs_from_state(
        final_state, account_equity=ACCOUNT_EQUITY, risk_per_trade_pct=0.5
    )
    plan = build_trade_plan(plan_inputs)
    print(f"TradePlan (from real TradingAgents decision): {plan}\n")

    csv_path = _find_cached_csv(SYMBOL)
    print(f"Loading bars from {csv_path}")

    instrument_id = InstrumentId(Symbol(SYMBOL), VENUE)
    bar_type = BarType(
        instrument_id=instrument_id,
        bar_spec=BarSpecification(1, BarAggregation.DAY, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )

    bars = _load_bars(csv_path, bar_type)
    print(f"Loaded {len(bars)} daily bars, {bars[0].ts_event} .. {bars[-1].ts_event}")

    equity = Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(SYMBOL),
        currency=INR,
        price_precision=PRICE_PRECISION,
        price_increment=Price(0.05, precision=PRICE_PRECISION),  # NSE tick size
        lot_size=Quantity(LOT_SIZE, precision=0),
        ts_event=bars[0].ts_event,
        ts_init=bars[0].ts_event,
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("PAPER-001"),
            logging=LoggingConfig(log_level="INFO"),
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=INR,
        starting_balances=[Money(500_000, INR)],
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

    print()
    print(engine.trader.generate_account_report(VENUE))
    print(engine.trader.generate_order_fills_report())
    print(engine.trader.generate_positions_report())

    engine.reset()
    engine.dispose()


if __name__ == "__main__":
    main()
