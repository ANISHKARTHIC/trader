"""SQLite-backed history for the companion: scans, decisions, and the
manual trade journal. See schema.sql for the full table design and the
rationale for keeping this separate from TradingAgents' own decision log.

One connection per call (SQLite handles concurrent readers/writers fine at
this write volume — a handful of rows per scan, not a hot path) rather than
a pooled connection, since this is a single-user local tool.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "verdict.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


# --- Scans ---


def create_scan(
    analysis_date: str,
    kind: str,
    screen_top_n: int | None = None,
    deep_analyze_top_n: int | None = None,
    account_equity: float | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO scans (analysis_date, kind, screen_top_n, deep_analyze_top_n, "
            "account_equity, started_at, status) VALUES (?, ?, ?, ?, ?, ?, 'running')",
            (analysis_date, kind, screen_top_n, deep_analyze_top_n, account_equity, _now()),
        )
        return cur.lastrowid


def complete_scan(scan_id: int, status: str = "done", error: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE scans SET status = ?, completed_at = ?, error = ? WHERE id = ?",
            (status, _now(), error, scan_id),
        )


def reconcile_stale_scans() -> int:
    """Mark any scan still 'running' as errored. Call once at process startup.

    A scan's background thread lives only as long as the process that
    started it — if the server is killed/restarted mid-scan (as happens
    routinely during development, or any crash), the in-memory job and its
    thread are gone, but nothing ever updates the DB row, so it stays
    "running" forever and the Home overview reports a scan in progress that
    isn't running anywhere. Since a fresh process start means no scan from
    a prior run can possibly still be executing, every "running" row at
    startup is stale by definition. Returns how many rows were fixed.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE scans SET status = 'error', completed_at = ?, "
            "error = 'Interrupted — the server restarted while this scan was running.' "
            "WHERE status = 'running'",
            (_now(),),
        )
        return cur.rowcount


def record_decision(
    scan_id: int,
    symbol: str,
    analysis_date: str,
    is_existing_holding: bool,
    action_label: str,
    rating: str,
    side: str,
    entry: float | None,
    stop: float | None,
    target: float | None,
    suggested_qty: int | None,
    risk_amount: float | None,
    screen_score: float | None,
    pm_decision_markdown: str,
    trader_proposal_markdown: str,
    market_report: str,
    fundamentals_report: str,
    holding_verdict: str | None = None,
    holding_qty: int | None = None,
    holding_avg_price: float | None = None,
    holding_last_price: float | None = None,
    holding_unrealized_pnl: float | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO decisions (
                scan_id, symbol, analysis_date, is_existing_holding, action_label,
                rating, side, entry, stop, target, suggested_qty, risk_amount,
                screen_score, holding_verdict, holding_qty, holding_avg_price,
                holding_last_price, holding_unrealized_pnl, pm_decision_markdown,
                trader_proposal_markdown, market_report, fundamentals_report, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_id, symbol, analysis_date, int(is_existing_holding), action_label,
                rating, side, entry, stop, target, suggested_qty, risk_amount,
                screen_score, holding_verdict, holding_qty, holding_avg_price,
                holding_last_price, holding_unrealized_pnl, pm_decision_markdown,
                trader_proposal_markdown, market_report, fundamentals_report, _now(),
            ),
        )
        return cur.lastrowid


def list_scans(limit: int = 30) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_scan(scan_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        return dict(row) if row else None


def list_decisions_for_scan(scan_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_decisions_for_symbol(symbol: str, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_decision(decision_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return dict(row) if row else None


# --- Journal ---


def add_journal_entry(
    symbol: str,
    entry_date: str,
    action_taken: str,
    decision_id: int | None = None,
    actual_qty: int | None = None,
    actual_price: float | None = None,
    notes: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO journal_entries (
                decision_id, symbol, entry_date, action_taken, actual_qty,
                actual_price, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, symbol, entry_date, action_taken, actual_qty,
             actual_price, notes, _now()),
        )
        return cur.lastrowid


def update_journal_outcome(entry_id: int, outcome_notes: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE journal_entries SET outcome_notes = ? WHERE id = ?",
            (outcome_notes, entry_id),
        )


def list_journal_entries(symbol: str | None = None, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM journal_entries WHERE symbol = ? "
                "ORDER BY entry_date DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM journal_entries ORDER BY entry_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# --- Chat ---


def add_chat_message(role: str, content: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, _now()),
        )
        return cur.lastrowid


def list_chat_messages(limit: int = 50) -> list[dict]:
    """Most recent `limit` messages, oldest first (chat display order)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_chat_messages() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_messages")
