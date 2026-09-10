"""The stock universe to screen: NSE's own Nifty 500 constituent list.

Fetched from NSE's static archive (not the bot-protected main site, which
blocks plain HTTP fetches). Nifty 500 covers ~95% of NSE free-float market
cap — a reasonable stand-in for "the whole market" that excludes mostly
illiquid microcaps not worth generating trade ideas for anyway.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from pathlib import Path

import requests

NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "nifty500.csv"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # NSE updates the index list infrequently


@dataclass(frozen=True)
class Instrument:
    symbol: str  # e.g. "RELIANCE" (no .NS suffix)
    company_name: str
    sector: str
    isin: str

    @property
    def yfinance_symbol(self) -> str:
        return f"{self.symbol}.NS"


def _fetch_fresh() -> str:
    resp = requests.get(
        NIFTY500_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text


def get_nifty500(force_refresh: bool = False) -> list[Instrument]:
    """Return the Nifty 500 constituents, using a 24h local cache."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_MAX_AGE_SECONDS:
            text = CACHE_PATH.read_text()
            return _parse(text)

    try:
        text = _fetch_fresh()
        CACHE_PATH.write_text(text)
    except requests.RequestException:
        if CACHE_PATH.exists():
            text = CACHE_PATH.read_text()  # stale cache beats no data
        else:
            raise

    return _parse(text)


def _parse(csv_text: str) -> list[Instrument]:
    reader = csv.DictReader(io.StringIO(csv_text))
    out = []
    for row in reader:
        out.append(
            Instrument(
                symbol=row["Symbol"].strip(),
                company_name=row["Company Name"].strip(),
                sector=row["Industry"].strip(),
                isin=row["ISIN Code"].strip(),
            )
        )
    return out
