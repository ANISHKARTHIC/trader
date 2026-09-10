"""A NautilusTrader Strategy that executes TradePlans as bracket orders.

This is the only piece of "execution" code in the project that is genuinely
new — everything it calls into (order factory, risk engine, backtest engine,
account/position tracking, fills/PnL reporting) is NautilusTrader's own,
unmodified. The strategy's job is narrow: take a TradePlan (produced by
agent/translator/trade_plan.py from a TradingAgents decision) and submit it
as a bracket order. NautilusTrader's RiskEngine sits between this strategy
and the venue on every submission and can reject the order outright — see
on_order_denied/on_order_rejected below, which is where that veto becomes
observable.

Live trading later reuses this exact strategy unchanged: swap the backtest
venue for a real venue/execution client and the same TradePlan -> bracket
order call fires against the real broker. That's the whole point of using
NautilusTrader instead of hand-rolling both a backtester and a live executor.
"""

from __future__ import annotations

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig

from agent.translator.trade_plan import Side, TradePlan


class TradePlanStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    price_precision: int = 2
    size_precision: int = 0


class TradePlanStrategy(Strategy):
    """Submits one externally-supplied TradePlan as a bracket order.

    Not a signal-generating strategy in the usual Nautilus sense — the
    signal already happened upstream (TradingAgents + the risk-sized
    translator). This class exists purely to cross the boundary from "a plan
    we've decided on" to "an order NautilusTrader's engine will risk-check,
    route, and track."

    The plan must be attached via `set_trade_plan()` before `engine.run()` —
    Nautilus strategies are constructed and registered before the clock
    starts, and `on_start()` is the first hook that runs inside the engine's
    event loop, so that's where a pre-attached plan actually gets submitted.
    """

    def __init__(self, config: TradePlanStrategyConfig) -> None:
        super().__init__(config)
        self._plan: TradePlan | None = None

    def set_trade_plan(self, plan: TradePlan) -> None:
        """Attach the plan to submit once the strategy starts. Call before `engine.run()`."""
        self._plan = plan

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument {self.config.instrument_id} not found in cache")
            self.stop()
            return
        if self._plan is not None:
            self.submit_trade_plan(self._plan)

    def submit_trade_plan(self, plan: TradePlan) -> None:
        """Queue a TradePlan for submission as a bracket order.

        Called externally (by the backtest driver script, or later by a live
        scheduler) once per decision. A FLAT plan (Hold, or a degenerate
        stop) is a deliberate no-op — TradingAgents said don't trade, or the
        translator refused to size a nonsensical stop, and both are valid
        reasons to do nothing rather than force an order.
        """
        if plan.side is Side.FLAT or plan.suggested_qty <= 0:
            self.log.info(f"No order for {plan.symbol}: side={plan.side}, qty={plan.suggested_qty}")
            return

        order_side = OrderSide.BUY if plan.side is Side.BUY else OrderSide.SELL
        p = self.config.price_precision
        s = self.config.size_precision

        bracket = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=Quantity(plan.suggested_qty, precision=s),
            entry_price=Price(round(plan.entry, p), precision=p),
            entry_order_type=OrderType.LIMIT,
            tp_price=Price(round(plan.target, p), precision=p),
            sl_trigger_price=Price(round(plan.stop, p), precision=p),
            time_in_force=TimeInForce.GTC,
        )
        self.log.info(
            f"Submitting bracket for {plan.symbol}: {plan.side.value} "
            f"qty={plan.suggested_qty} entry={plan.entry} stop={plan.stop} "
            f"target={plan.target} risk={plan.risk_amount:.2f} "
            f"(rating={plan.source_rating})"
        )
        self.submit_order_list(bracket)

    def on_order_denied(self, event) -> None:
        # This is the deterministic risk-engine veto: the plan the LLM/
        # translator wanted never reached the venue at all.
        self.log.warning(f"RISK ENGINE DENIED order: {event}")

    def on_order_rejected(self, event) -> None:
        self.log.warning(f"Venue REJECTED order: {event}")

    def on_order_filled(self, event) -> None:
        self.log.info(f"FILLED: {event}")
