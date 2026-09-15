import pytest

from agent.db import store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect the DB to a temp file for every test — never touch the real one."""
    test_db = tmp_path / "test_verdict.db"
    monkeypatch.setattr(store, "DB_PATH", test_db)
    store.init_db()
    yield


def test_create_and_complete_scan():
    scan_id = store.create_scan("2026-01-01", "today", screen_top_n=30, deep_analyze_top_n=8)
    scan = store.get_scan(scan_id)
    assert scan["status"] == "running"
    assert scan["analysis_date"] == "2026-01-01"

    store.complete_scan(scan_id, status="done")
    scan = store.get_scan(scan_id)
    assert scan["status"] == "done"
    assert scan["completed_at"] is not None


def test_record_and_list_decisions():
    scan_id = store.create_scan("2026-01-01", "today")
    did = store.record_decision(
        scan_id=scan_id, symbol="RELIANCE", analysis_date="2026-01-01",
        is_existing_holding=True, action_label="Trim position", rating="Hold",
        side="FLAT", entry=None, stop=None, target=None, suggested_qty=None,
        risk_amount=None, screen_score=None, pm_decision_markdown="**Rating**: Hold",
        trader_proposal_markdown="**Action**: Hold", market_report="x",
        fundamentals_report="y", holding_verdict="Trim", holding_qty=10,
        holding_avg_price=1250.0, holding_last_price=1271.0, holding_unrealized_pnl=210.0,
    )
    decisions = store.list_decisions_for_scan(scan_id)
    assert len(decisions) == 1
    assert decisions[0]["id"] == did
    assert decisions[0]["symbol"] == "RELIANCE"
    assert decisions[0]["holding_unrealized_pnl"] == 210.0


def test_scan_cascade_deletes_decisions():
    scan_id = store.create_scan("2026-01-01", "today")
    store.record_decision(
        scan_id=scan_id, symbol="TCS", analysis_date="2026-01-01",
        is_existing_holding=False, action_label="Buy (new position)", rating="Buy",
        side="BUY", entry=3800.0, stop=3720.0, target=4020.0, suggested_qty=25,
        risk_amount=2500.0, screen_score=45.2, pm_decision_markdown="x",
        trader_proposal_markdown="y", market_report="z", fundamentals_report="w",
    )
    import sqlite3
    with sqlite3.connect(store.DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        conn.commit()
    assert store.list_decisions_for_scan(scan_id) == []


def test_journal_entry_lifecycle():
    entry_id = store.add_journal_entry(
        symbol="RELIANCE", entry_date="2026-01-01", action_taken="followed",
        actual_qty=10, actual_price=1271.0, notes="took the call",
    )
    entries = store.list_journal_entries(symbol="RELIANCE")
    assert len(entries) == 1
    assert entries[0]["action_taken"] == "followed"
    assert entries[0]["outcome_notes"] is None

    store.update_journal_outcome(entry_id, "worked out, hit target")
    entries = store.list_journal_entries(symbol="RELIANCE")
    assert entries[0]["outcome_notes"] == "worked out, hit target"


def test_journal_rejects_invalid_action_taken_at_db_level():
    with pytest.raises(Exception):
        import sqlite3
        with sqlite3.connect(store.DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO journal_entries (symbol, entry_date, action_taken, created_at) "
                "VALUES ('X', '2026-01-01', 'bogus', 'now')"
            )
            conn.commit()


def test_reconcile_stale_scans_marks_running_as_error():
    stuck_id = store.create_scan("2026-01-01", "today")
    done_id = store.create_scan("2026-01-02", "today")
    store.complete_scan(done_id, status="done")

    fixed_count = store.reconcile_stale_scans()

    assert fixed_count == 1
    stuck = store.get_scan(stuck_id)
    assert stuck["status"] == "error"
    assert stuck["completed_at"] is not None
    assert "restarted" in stuck["error"]

    done = store.get_scan(done_id)
    assert done["status"] == "done"  # untouched


def test_reconcile_stale_scans_is_a_noop_when_nothing_stuck():
    scan_id = store.create_scan("2026-01-01", "today")
    store.complete_scan(scan_id, status="done")

    assert store.reconcile_stale_scans() == 0
