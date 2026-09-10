"""End-to-end demo: TradingAgents decision -> TradePlan -> NautilusTrader
paper-trading backtest with real risk gating.

This is a smoke test proving the full chain works, not a strategy backtest
in the proper sense (see Phase 14 of the research brief for what a real
walk-forward backtest needs — this script trades on exactly one decision).

Data: reuses the OHLCV CSV that TradingAgents itself cached to
~/.tradingagents/cache/ during the RELIANCE.NS analysis run, so there is no
new data dependency.

Run:
    python -m agent.execution.run_paper_backtest
"""

from __future__ import annotations

import glob
from decimal import Decimal
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
from agent.translator.trade_plan import TradePlanInputs, build_trade_plan

CACHE_DIR = Path.home() / ".tradingagents" / "cache"
VENUE = Venue("NSE")
SYMBOL = "RELIANCE"
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


def main() -> None:
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

    # --- Build one TradePlan from the last bar + a realized ATR, and attach
    # it to the strategy before running. In the real system this comes from
    # TradingAgents' PortfolioDecision/TraderProposal for the same date; here
    # it's forced to BUY so the demo actually exercises order submission,
    # risk-engine gating, and fills instead of a silent Hold no-op.
    last_bar = bars[-1]
    recent = [float(b.high) - float(b.low) for b in bars[-14:]]
    atr_14 = sum(recent) / len(recent)

    plan = build_trade_plan(
        TradePlanInputs(
            symbol=f"{SYMBOL}.NS",
            rating="Buy",
            last_price=float(last_bar.close),
            atr_14=atr_14,
            account_equity=500_000.0,
            risk_per_trade_pct=0.5,
        )
    )
    print(f"TradePlan: {plan}")

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
