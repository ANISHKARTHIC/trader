from agent.translator.trade_plan import Side, TradePlanInputs, build_trade_plan


def test_buy_rating_sizes_by_atr_risk():
    inp = TradePlanInputs(
        symbol="RELIANCE.NS",
        rating="Buy",
        last_price=1400.0,
        atr_14=20.0,
        account_equity=500_000.0,
        risk_per_trade_pct=0.5,  # risk_amount = 2500
        atr_stop_multiple=2.0,  # stop_distance = 40
        reward_risk_ratio=2.0,
    )
    plan = build_trade_plan(inp)
    assert plan.side is Side.BUY
    assert plan.entry == 1400.0
    assert plan.stop == 1360.0
    assert plan.stop_distance == 40.0
    assert plan.target == 1480.0  # entry + 2 * 40
    assert plan.risk_amount == 2500.0
    assert plan.suggested_qty == 62  # 2500 // 40


def test_hold_rating_produces_flat_zero_qty():
    inp = TradePlanInputs(
        symbol="TCS.NS",
        rating="Hold",
        last_price=3800.0,
        atr_14=50.0,
        account_equity=500_000.0,
    )
    plan = build_trade_plan(inp)
    assert plan.side is Side.FLAT
    assert plan.suggested_qty == 0


def test_llm_stop_tighter_than_atr_is_respected():
    inp = TradePlanInputs(
        symbol="INFY.NS",
        rating="Buy",
        last_price=1500.0,
        atr_14=15.0,  # atr stop distance = 30 -> atr stop = 1470
        account_equity=500_000.0,
        llm_stop_loss=1485.0,  # tighter (safer) than 1470
    )
    plan = build_trade_plan(inp)
    assert plan.stop == 1485.0  # max(1470, 1485) for a BUY


def test_llm_stop_looser_than_atr_is_overridden():
    inp = TradePlanInputs(
        symbol="INFY.NS",
        rating="Buy",
        last_price=1500.0,
        atr_14=15.0,  # atr stop = 1470
        account_equity=500_000.0,
        llm_stop_loss=1400.0,  # looser/riskier than ATR stop
    )
    plan = build_trade_plan(inp)
    assert plan.stop == 1470.0  # ATR stop wins, not the looser LLM one


def test_sell_rating_mirrors_buy_logic():
    inp = TradePlanInputs(
        symbol="ITC.NS",
        rating="Sell",
        last_price=450.0,
        atr_14=5.0,
        account_equity=500_000.0,
        atr_stop_multiple=2.0,
        reward_risk_ratio=2.0,
    )
    plan = build_trade_plan(inp)
    assert plan.side is Side.SELL
    assert plan.stop == 460.0  # entry + 2*5
    assert plan.target == 430.0  # entry - 2*stop_distance(10)


def test_degenerate_stop_distance_refuses_to_size():
    inp = TradePlanInputs(
        symbol="X",
        rating="Buy",
        last_price=100.0,
        atr_14=10.0,
        account_equity=100_000.0,
        llm_entry_price=100.0,
        llm_stop_loss=150.0,  # nonsensical: "stop" above entry on a BUY
    )
    plan = build_trade_plan(inp)
    assert plan.side is Side.FLAT
    assert plan.suggested_qty == 0
