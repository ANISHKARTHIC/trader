from agent.holding_eval import _hard_levels


def test_hard_levels_computed_from_entry_not_current_price():
    # Entry at 1000, ATR 20, default 2x ATR stop / 2:1 reward
    stop, target = _hard_levels(entry_price=1000.0, atr_14=20.0)
    assert stop == 960.0  # 1000 - 2*20
    assert target == 1080.0  # 1000 + 2*(2*20)


def test_hard_levels_respect_custom_multiples():
    stop, target = _hard_levels(
        entry_price=500.0, atr_14=10.0, atr_stop_multiple=1.5, reward_risk_ratio=3.0
    )
    assert stop == 485.0  # 500 - 1.5*10
    assert target == 545.0  # 500 + 3*(1.5*10)
