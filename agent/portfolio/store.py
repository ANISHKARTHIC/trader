"""Manual portfolio tracking: what you actually hold, entered by hand.

No broker connection yet (see project history — live Kite execution is
deliberately deferred). This is deliberately the simplest thing that works:
a JSON file. One user, one machine, low write volume — a database would be
solving a problem this doesn't have yet.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

STORE_PATH = Path(__file__).parent.parent.parent / "data" / "portfolio.json"
_lock = threading.Lock()


@dataclass
class Holding:
    symbol: str  # e.g. "RELIANCE" (no .NS suffix)
    quantity: int
    avg_price: float

    @property
    def yfinance_symbol(self) -> str:
        return f"{self.symbol}.NS"


def _read_all() -> dict[str, Holding]:
    if not STORE_PATH.exists():
        return {}
    raw = json.loads(STORE_PATH.read_text())
    return {s: Holding(**h) for s, h in raw.items()}


def _write_all(holdings: dict[str, Holding]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = {s: asdict(h) for s, h in holdings.items()}
    STORE_PATH.write_text(json.dumps(raw, indent=2))


def list_holdings() -> list[Holding]:
    with _lock:
        return list(_read_all().values())


def upsert_holding(symbol: str, quantity: int, avg_price: float) -> Holding:
    """Add or replace a holding. quantity=0 removes it."""
    symbol = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    with _lock:
        holdings = _read_all()
        if quantity <= 0:
            holdings.pop(symbol, None)
            _write_all(holdings)
            return Holding(symbol=symbol, quantity=0, avg_price=avg_price)
        holding = Holding(symbol=symbol, quantity=quantity, avg_price=avg_price)
        holdings[symbol] = holding
        _write_all(holdings)
        return holding


def remove_holding(symbol: str) -> None:
    symbol = symbol.strip().upper().removesuffix(".NS").removesuffix(".BO")
    with _lock:
        holdings = _read_all()
        holdings.pop(symbol, None)
        _write_all(holdings)
