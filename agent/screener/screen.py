"""Stage 1 of the companion: a fast, free, deterministic screen across the
Nifty 500 universe to shortlist candidates for deep (LLM-driven) analysis.

No LLM calls here — this is pure price/volume math, same principle as
Batch 1/Phase 3 of the original research brief ("indicators are deterministic
math, not LLM output"). The only job of this module is to turn ~500 tickers
into a ranked shortlist of a few dozen worth spending TradingAgents' LLM
budget on.

Signals used, all standard/well-understood, each independently interpretable:
- momentum_21d: 21-trading-day (~1 month) return
- rsi_14: 14-period RSI (stockstats) — flags overbought/oversold
- volume_ratio: today's volume vs 20-day average — flags unusual activity
- above_50sma / above_200sma: trend-following context
- macd_hist: MACD histogram sign/magnitude — momentum acceleration

A single "screen_score" combines these into one rank; it is a heuristic, not
a validated alpha signal — see Phase 14/15 of the research brief on what a
real backtested screen would require before being trusted with size.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf
from stockstats import StockDataFrame

from agent.screener.universe import Instrument, get_nifty500

BATCH_SIZE = 50  # yfinance bulk-download batch size; keeps single requests reasonable
LOOKBACK_PERIOD = "6mo"  # enough history for 200-SMA + RSI + MACD to stabilize


@dataclass
class ScreenResult:
    symbol: str
    company_name: str
    sector: str
    last_close: float
    momentum_21d: float
    rsi_14: float
    volume_ratio: float
    above_50sma: bool
    above_200sma: bool
    macd_hist: float
    screen_score: float


def _score(r: dict) -> float:
    """Heuristic combination — see module docstring caveat.

    Rewards: positive momentum, RSI in a constructive-not-overbought band,
    unusual volume (either direction — a spike is informative either way),
    trend alignment (above both SMAs), and positive/accelerating MACD.
    """
    score = 0.0
    score += max(min(r["momentum_21d"], 0.30), -0.30) * 100  # cap influence of outliers
    if 45 <= r["rsi_14"] <= 70:
        score += 10
    elif r["rsi_14"] > 80 or r["rsi_14"] < 20:
        score -= 10
    if r["volume_ratio"] > 1.5:
        score += 8
    if r["above_50sma"]:
        score += 5
    if r["above_200sma"]:
        score += 5
    score += max(min(r["macd_hist"], 5), -5)
    return round(score, 2)


def _screen_one(symbol: str, df: pd.DataFrame) -> dict | None:
    if df is None or df.empty or len(df) < 60:
        return None
    # A symbol yfinance couldn't resolve at all (delisted/renamed/never
    # existed — e.g. a stale entry in NSE's own Nifty 500 list) comes back
    # as an all-NaN frame whose columns are tuples, not plain strings; the
    # rename/select below would raise on that shape, so bail out first.
    if not all(isinstance(c, str) for c in df.columns):
        return None
    df = df.dropna(how="all")
    if df.empty or len(df) < 60:
        return None
    lower_cols = {c.lower() for c in df.columns}
    if not {"open", "high", "low", "close", "volume"}.issubset(lower_cols):
        return None
    sdf = StockDataFrame.retype(
        df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    )
    rsi = sdf["rsi_14"]
    macd_hist = sdf["macdh"]
    close = df["Close"]
    volume = df["Volume"]

    if len(close) < 21 or close.iloc[-21] == 0:
        return None

    momentum_21d = float(close.iloc[-1] / close.iloc[-21] - 1)
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    avg_vol_20 = volume.tail(20).mean()

    return {
        "symbol": symbol,
        "last_close": float(close.iloc[-1]),
        "momentum_21d": momentum_21d,
        "rsi_14": float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0,
        "volume_ratio": float(volume.iloc[-1] / avg_vol_20) if avg_vol_20 else 1.0,
        "above_50sma": bool(sma50 is not None and close.iloc[-1] > sma50),
        "above_200sma": bool(sma200 is not None and close.iloc[-1] > sma200),
        "macd_hist": float(macd_hist.iloc[-1]) if not pd.isna(macd_hist.iloc[-1]) else 0.0,
    }


def run_screen(
    top_n: int = 30,
    universe: list[Instrument] | None = None,
    progress_cb=None,
) -> list[ScreenResult]:
    """Screen the Nifty 500 (or a supplied universe) and return the top_n ranked results.

    `progress_cb(done, total)` is called after each batch, if supplied — the
    web dashboard uses this to show scan progress since this takes ~1-2
    minutes across the full universe (bulk yfinance calls, no LLM).
    """
    instruments = universe if universe is not None else get_nifty500()
    by_symbol = {i.symbol: i for i in instruments}
    yf_symbols = [i.yfinance_symbol for i in instruments]

    results: list[dict] = []
    total = len(yf_symbols)
    for start in range(0, total, BATCH_SIZE):
        batch = yf_symbols[start : start + BATCH_SIZE]
        data = yf.download(
            batch, period=LOOKBACK_PERIOD, group_by="ticker", progress=False, threads=True
        )
        for yf_symbol in batch:
            symbol = yf_symbol.removesuffix(".NS")
            try:
                df = data[yf_symbol] if len(batch) > 1 else data
                row = _screen_one(symbol, df)
            except (KeyError, ValueError):
                # A symbol yfinance couldn't resolve (delisted/renamed/never
                # existed) shouldn't take down the whole screen — skip it.
                continue
            if row is not None:
                results.append(row)
        if progress_cb:
            progress_cb(min(start + BATCH_SIZE, total), total)

    for row in results:
        row["screen_score"] = _score(row)

    results.sort(key=lambda r: r["screen_score"], reverse=True)

    out = []
    for row in results[:top_n]:
        inst = by_symbol.get(row["symbol"])
        out.append(
            ScreenResult(
                symbol=row["symbol"],
                company_name=inst.company_name if inst else row["symbol"],
                sector=inst.sector if inst else "Unknown",
                last_close=row["last_close"],
                momentum_21d=row["momentum_21d"],
                rsi_14=row["rsi_14"],
                volume_ratio=row["volume_ratio"],
                above_50sma=row["above_50sma"],
                above_200sma=row["above_200sma"],
                macd_hist=row["macd_hist"],
                screen_score=row["screen_score"],
            )
        )
    return out
