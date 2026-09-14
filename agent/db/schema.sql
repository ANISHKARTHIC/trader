-- Verdict's own history/journal database.
--
-- This is deliberately separate from TradingAgents' own decision log
-- (~/.tradingagents/memory/trading_memory.md, which already resolves
-- realized returns vs. NSEI/BSESN and writes a reflection per decision —
-- see agent/db/learning.py for how this schema surfaces that data rather
-- than re-implementing it).
--
-- What this schema adds that TradingAgents' log doesn't have:
--   1. Structured, queryable storage (SQLite, not markdown regex-parsing)
--   2. Scan-level grouping (a "Today" run's full ranked action list, not
--      just per-ticker entries)
--   3. A manual journal: what the user actually did, separate from what
--      the AI recommended or what its own paper-trade did.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('today', 'single')),
    screen_top_n INTEGER,
    deep_analyze_top_n INTEGER,
    account_equity REAL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'error')),
    error TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    is_existing_holding INTEGER NOT NULL DEFAULT 0,
    action_label TEXT NOT NULL,
    rating TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL,
    stop REAL,
    target REAL,
    suggested_qty INTEGER,
    risk_amount REAL,
    screen_score REAL,
    holding_verdict TEXT,
    holding_qty INTEGER,
    holding_avg_price REAL,
    holding_last_price REAL,
    holding_unrealized_pnl REAL,
    pm_decision_markdown TEXT,
    trader_proposal_markdown TEXT,
    market_report TEXT,
    fundamentals_report TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_scan ON decisions(scan_id);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(analysis_date);

-- One row per manual journal note the user attaches to a decision: did they
-- follow the call, ignore it, do something else entirely, and why. This is
-- the "what actually happened to my money" signal, distinct from
-- TradingAgents' own "was the rating right" reflection.
CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER REFERENCES decisions(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    action_taken TEXT NOT NULL CHECK (
        action_taken IN ('followed', 'ignored', 'modified', 'other')
    ),
    actual_qty INTEGER,
    actual_price REAL,
    notes TEXT,
    outcome_notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_symbol ON journal_entries(symbol);
CREATE INDEX IF NOT EXISTS idx_journal_decision ON journal_entries(decision_id);

-- Chat history — single-user local tool, so one running conversation
-- (no multi-session/multi-user concept) that persists across dashboard
-- restarts, same reasoning as scans/decisions above.
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(created_at);
