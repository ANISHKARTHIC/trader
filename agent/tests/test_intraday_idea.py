from agent.intraday_idea import BUY_SCORE_THRESHOLD, SELL_SCORE_THRESHOLD, get_budget_trade_idea


def test_too_expensive_when_budget_below_one_share(monkeypatch):
    monkeypatch.setattr(
        "agent.intraday_idea._screen_one",
        lambda symbol, df: {
            "symbol": symbol, "last_close": 1500.0, "momentum_21d": 0.1, "rsi_14": 55.0,
            "volume_ratio": 1.0, "above_50sma": True, "above_200sma": True,
            "macd_hist": 1.0, "near_52w_high": False, "near_52w_low": False, "bband_squeeze": False,
        },
    )
    monkeypatch.setattr("agent.intraday_idea._score", lambda row: 20.0)
    monkeypatch.setattr("agent.intraday_idea.yf.Ticker", lambda s: _FakeTicker(last_price=1500.0))

    idea = get_budget_trade_idea("EXPENSIVE", 100.0)
    assert idea.verdict == "too_expensive"
    assert idea.quantity == 0


def test_avoid_when_score_is_weak(monkeypatch):
    monkeypatch.setattr(
        "agent.intraday_idea._screen_one",
        lambda symbol, df: {
            "symbol": symbol, "last_close": 50.0, "momentum_21d": 0.0, "rsi_14": 50.0,
            "volume_ratio": 1.0, "above_50sma": True, "above_200sma": False,
            "macd_hist": 0.0, "near_52w_high": False, "near_52w_low": False, "bband_squeeze": False,
        },
    )
    monkeypatch.setattr("agent.intraday_idea._score", lambda row: 0.0)
    monkeypatch.setattr("agent.intraday_idea.yf.Ticker", lambda s: _FakeTicker(last_price=50.0))

    idea = get_budget_trade_idea("FLAT", 1000.0)
    assert idea.verdict == "avoid"
    assert idea.quantity == 0


def test_buy_sizes_quantity_within_budget(monkeypatch):
    monkeypatch.setattr(
        "agent.intraday_idea._screen_one",
        lambda symbol, df: {
            "symbol": symbol, "last_close": 20.0, "momentum_21d": 0.08, "rsi_14": 55.0,
            "volume_ratio": 1.2, "above_50sma": True, "above_200sma": True,
            "macd_hist": 1.0, "near_52w_high": False, "near_52w_low": False, "bband_squeeze": False,
        },
    )
    monkeypatch.setattr("agent.intraday_idea._score", lambda row: BUY_SCORE_THRESHOLD + 5)
    monkeypatch.setattr("agent.intraday_idea.yf.Ticker", lambda s: _FakeTicker(last_price=20.0))
    monkeypatch.setattr(
        "agent.intraday_idea.StockDataFrame.retype",
        lambda df: {"atr": _FakeSeries(1.0)},
    )

    idea = get_budget_trade_idea("CHEAP", 100.0)
    assert idea.verdict == "buy"
    assert idea.entry == 20.0
    assert idea.stop < idea.entry  # buy stop sits below entry
    assert idea.target > idea.entry
    assert idea.quantity == 5  # floor(100 / 20)
    assert idea.cost <= 100.0


def test_sell_stop_sits_above_entry(monkeypatch):
    monkeypatch.setattr(
        "agent.intraday_idea._screen_one",
        lambda symbol, df: {
            "symbol": symbol, "last_close": 20.0, "momentum_21d": -0.08, "rsi_14": 25.0,
            "volume_ratio": 1.2, "above_50sma": False, "above_200sma": False,
            "macd_hist": -1.0, "near_52w_high": False, "near_52w_low": True, "bband_squeeze": False,
        },
    )
    monkeypatch.setattr("agent.intraday_idea._score", lambda row: SELL_SCORE_THRESHOLD - 5)
    monkeypatch.setattr("agent.intraday_idea.yf.Ticker", lambda s: _FakeTicker(last_price=20.0))
    monkeypatch.setattr(
        "agent.intraday_idea.StockDataFrame.retype",
        lambda df: {"atr": _FakeSeries(1.0)},
    )

    idea = get_budget_trade_idea("SHORTME", 100.0)
    assert idea.verdict == "sell"
    assert idea.stop > idea.entry
    assert idea.target < idea.entry


class _FakeFastInfo:
    def __init__(self, last_price):
        self.last_price = last_price


class _FakeTicker:
    def __init__(self, last_price):
        self.fast_info = _FakeFastInfo(last_price)

    def history(self, period=None):
        import pandas as pd
        return pd.DataFrame(
            {"Open": [1] * 60, "High": [1] * 60, "Low": [1] * 60, "Close": [1] * 60, "Volume": [1] * 60}
        )


class _FakeSeries(list):
    def __init__(self, value):
        super().__init__([value])

    @property
    def iloc(self):
        return self
