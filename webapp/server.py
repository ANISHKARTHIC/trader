"""Local web dashboard for the trading agent.

Runs analyses as background jobs (TradingAgents calls take minutes — an LLM
per analyst, per debate round) and exposes them over a small JSON API that
the static frontend polls. In-memory job store: fine for a single-user local
tool; would need a real queue/DB the moment more than one person or process
touches it.

Run:
    uvicorn webapp.server:app --reload --port 8000
Then open http://localhost:8000
"""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.pipeline import run_full_pipeline
from agent.companion import build_today
from agent.notify.daily_report import send_daily_report
from agent.portfolio.store import list_holdings, upsert_holding, remove_holding
from agent.db.store import (
    init_db, create_scan, complete_scan, record_decision,
    list_scans, get_scan, list_decisions_for_scan,
    add_journal_entry, list_journal_entries,
)
from agent.db.learning import get_reflections, get_performance_summary, get_journal_with_reflections
from agent.settings import get_settings, update_settings, KNOWN_OLLAMA_MODELS

logger = logging.getLogger(__name__)

init_db()

app = FastAPI(title="Trading Agent Dashboard")

STATIC_DIR = Path(__file__).parent / "static"

JobStatus = Literal["queued", "running", "done", "error"]


class Job:
    def __init__(self, job_id: str, symbol: str, analysis_date: str, mode: str = "quick"):
        self.id = job_id
        self.symbol = symbol
        self.analysis_date = analysis_date
        self.mode = mode
        self.status: JobStatus = "queued"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.result: dict | None = None
        self.error: str | None = None


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


class AnalyzeRequest(BaseModel):
    symbol: str  # e.g. "RELIANCE" or "RELIANCE.NS"
    analysis_date: str  # "YYYY-MM-DD"
    account_equity: float = 500_000.0
    mode: str = "quick"  # "quick" or "deep" — see agent.pipeline.ANALYSIS_MODES


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol.endswith((".NS", ".BO")):
        symbol += ".NS"
    return symbol


def _run_job(job: Job, account_equity: float, mode: str) -> None:
    job.status = "running"
    try:
        result = run_full_pipeline(
            job.symbol,
            job.analysis_date,
            account_equity=account_equity,
            mode=mode,
        )
        result_dict = asdict(result)
        result_dict["trade_plan"]["side"] = result.trade_plan.side.value
        job.result = result_dict
        job.status = "done"
    except Exception:
        job.error = traceback.format_exc()
        job.status = "error"


@app.post("/api/analyze")
def start_analysis(req: AnalyzeRequest) -> dict:
    symbol = _normalize_symbol(req.symbol)
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, symbol, req.analysis_date, mode=req.mode)
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job, args=(job, req.account_equity, req.mode), daemon=True
    )
    thread.start()

    return {"job_id": job_id, "status": job.status}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.id,
        "symbol": job.symbol,
        "analysis_date": job.analysis_date,
        "mode": job.mode,
        "status": job.status,
        "created_at": job.created_at,
        "result": job.result,
        "error": job.error,
    }


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    with _jobs_lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [
        {
            "job_id": j.id,
            "symbol": j.symbol,
            "analysis_date": j.analysis_date,
            "mode": j.mode,
            "status": j.status,
            "created_at": j.created_at,
            "rating": (j.result or {}).get("trade_plan", {}).get("source_rating"),
        }
        for j in jobs
    ]


class TodayJob:
    def __init__(
        self, job_id: str, analysis_date: str, screen_top_n: int, deep_top_n: int,
        mode: str = "quick",
    ):
        self.id = job_id
        self.analysis_date = analysis_date
        self.screen_top_n = screen_top_n
        self.deep_top_n = deep_top_n
        self.mode = mode
        self.status: JobStatus = "queued"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.stage = "queued"
        self.progress_done = 0
        self.progress_total = 0
        self.result: list[dict] | None = None
        self.error: str | None = None


_today_jobs: dict[str, TodayJob] = {}
_today_jobs_lock = threading.Lock()


class TodayRequest(BaseModel):
    analysis_date: str | None = None
    screen_top_n: int = 30
    deep_analyze_top_n: int = 8
    account_equity: float = 500_000.0
    mode: str = "quick"  # "quick" or "deep" — see agent.pipeline.ANALYSIS_MODES


def _run_today_job(job: TodayJob, account_equity: float) -> None:
    job.status = "running"
    scan_id = create_scan(
        job.analysis_date, "today",
        screen_top_n=job.screen_top_n, deep_analyze_top_n=job.deep_top_n,
        account_equity=account_equity,
    )

    def progress_cb(stage: str, done: int, total: int) -> None:
        job.stage = stage
        job.progress_done = done
        job.progress_total = total

    try:
        actions = build_today(
            analysis_date=job.analysis_date,
            screen_top_n=job.screen_top_n,
            deep_analyze_top_n=job.deep_top_n,
            account_equity=account_equity,
            progress_cb=progress_cb,
            max_concurrent_analyses=get_settings().max_concurrent_analyses,
            mode=job.mode,
        )
        out = []
        for a in actions:
            result_dict = asdict(a.result)
            result_dict["trade_plan"]["side"] = a.result.trade_plan.side.value
            holding_dict = None
            if a.holding_eval is not None:
                he = a.holding_eval
                holding_dict = {
                    "quantity": he.holding.quantity,
                    "avg_price": he.holding.avg_price,
                    "last_price": he.last_price,
                    "unrealized_pnl": he.unrealized_pnl,
                    "unrealized_pnl_pct": he.unrealized_pnl_pct,
                    "hard_stop": he.hard_stop,
                    "hard_target": he.hard_target,
                    "verdict": he.verdict.value,
                }
            plan = a.result.trade_plan
            record_decision(
                scan_id=scan_id, symbol=a.symbol, analysis_date=job.analysis_date,
                is_existing_holding=a.is_existing_holding, action_label=a.label.value,
                rating=plan.source_rating, side=plan.side.value,
                entry=plan.entry if plan.side.value != "FLAT" else None,
                stop=plan.stop if plan.side.value != "FLAT" else None,
                target=plan.target if plan.side.value != "FLAT" else None,
                suggested_qty=plan.suggested_qty, risk_amount=plan.risk_amount,
                screen_score=a.screen_score,
                pm_decision_markdown=a.result.pm_decision_markdown,
                trader_proposal_markdown=a.result.trader_proposal_markdown,
                market_report=a.result.market_report,
                fundamentals_report=a.result.fundamentals_report,
                holding_verdict=a.holding_eval.verdict.value if a.holding_eval else None,
                holding_qty=a.holding_eval.holding.quantity if a.holding_eval else None,
                holding_avg_price=a.holding_eval.holding.avg_price if a.holding_eval else None,
                holding_last_price=a.holding_eval.last_price if a.holding_eval else None,
                holding_unrealized_pnl=a.holding_eval.unrealized_pnl if a.holding_eval else None,
            )
            out.append(
                {
                    "symbol": a.symbol,
                    "label": a.label.value,
                    "is_existing_holding": a.is_existing_holding,
                    "screen_score": a.screen_score,
                    "result": result_dict,
                    "holding_eval": holding_dict,
                }
            )
        job.result = out
        complete_scan(scan_id, status="done")
        job.status = "done"
        try:
            send_daily_report(actions, job.analysis_date)
        except Exception:
            logger.warning("Telegram daily report failed to send", exc_info=True)
    except Exception:
        job.error = traceback.format_exc()
        job.status = "error"
        complete_scan(scan_id, status="error", error=job.error)


@app.post("/api/today")
def start_today(req: TodayRequest) -> dict:
    job_id = uuid.uuid4().hex[:12]
    analysis_date = req.analysis_date or datetime.now().date().isoformat()
    job = TodayJob(job_id, analysis_date, req.screen_top_n, req.deep_analyze_top_n, mode=req.mode)
    with _today_jobs_lock:
        _today_jobs[job_id] = job

    thread = threading.Thread(
        target=_run_today_job, args=(job, req.account_equity), daemon=True
    )
    thread.start()
    return {"job_id": job_id, "status": job.status}


@app.get("/api/today/{job_id}")
def get_today(job_id: str) -> dict:
    with _today_jobs_lock:
        job = _today_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.id,
        "analysis_date": job.analysis_date,
        "mode": job.mode,
        "status": job.status,
        "stage": job.stage,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "created_at": job.created_at,
        "result": job.result,
        "error": job.error,
    }


@app.get("/api/today")
def list_today_jobs() -> list[dict]:
    with _today_jobs_lock:
        jobs = list(_today_jobs.values())
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [
        {
            "job_id": j.id,
            "analysis_date": j.analysis_date,
            "status": j.status,
            "created_at": j.created_at,
        }
        for j in jobs
    ]


class HoldingRequest(BaseModel):
    symbol: str
    quantity: int
    avg_price: float


@app.get("/api/portfolio")
def get_portfolio() -> list[dict]:
    return [asdict(h) for h in list_holdings()]


@app.post("/api/portfolio")
def add_holding(req: HoldingRequest) -> dict:
    holding = upsert_holding(req.symbol, req.quantity, req.avg_price)
    return asdict(holding)


@app.delete("/api/portfolio/{symbol}")
def delete_holding(symbol: str) -> dict:
    remove_holding(symbol)
    return {"removed": symbol.upper()}


# --- Scan history / reports ---


@app.get("/api/history/scans")
def get_scan_history(limit: int = 30) -> list[dict]:
    return list_scans(limit=limit)


@app.get("/api/history/scans/{scan_id}")
def get_scan_report(scan_id: int) -> dict:
    scan = get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    decisions = list_decisions_for_scan(scan_id)
    return {"scan": scan, "decisions": decisions}


# --- Journal ---


class JournalRequest(BaseModel):
    symbol: str
    entry_date: str
    action_taken: str  # "followed" | "ignored" | "modified" | "other"
    decision_id: int | None = None
    actual_qty: int | None = None
    actual_price: float | None = None
    notes: str | None = None


@app.post("/api/journal")
def create_journal_entry(req: JournalRequest) -> dict:
    if req.action_taken not in ("followed", "ignored", "modified", "other"):
        raise HTTPException(status_code=400, detail="invalid action_taken")
    entry_id = add_journal_entry(
        symbol=req.symbol.upper(), entry_date=req.entry_date,
        action_taken=req.action_taken, decision_id=req.decision_id,
        actual_qty=req.actual_qty, actual_price=req.actual_price, notes=req.notes,
    )
    return {"id": entry_id}


@app.get("/api/journal")
def get_journal(symbol: str | None = None, limit: int = 100) -> list[dict]:
    return get_journal_with_reflections(symbol=symbol, limit=limit)


# --- Learning / reflections (surfaces TradingAgents' own outcome log) ---


@app.get("/api/learning/reflections")
def get_learning_reflections(symbol: str | None = None, limit: int = 100) -> list[dict]:
    return [
        {
            "date": r.date, "ticker": r.ticker, "rating": r.rating, "pending": r.pending,
            "raw_return": r.raw_return, "alpha_return": r.alpha_return,
            "holding_days": r.holding_days, "resolved_date": r.resolved_date,
            "reflection": r.reflection,
        }
        for r in get_reflections(symbol=symbol, resolved_only=True, limit=limit)
    ]


@app.get("/api/learning/summary")
def get_learning_summary() -> dict:
    return get_performance_summary()


# --- Settings ---


def _mask(secret: str) -> str:
    """Never send a full secret back to the browser once it's set — show
    just enough to confirm it's configured (e.g. sk-...a1b2) without letting
    a page inspector or screen-share leak the whole key/token.
    """
    if not secret:
        return ""
    if len(secret) <= 8:
        return "•" * len(secret)
    return secret[:4] + "…" + secret[-4:]


class SettingsRequest(BaseModel):
    llm_provider: str | None = None
    ollama_base_url: str | None = None
    deep_think_model: str | None = None
    quick_think_model: str | None = None
    llm_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    max_concurrent_analyses: int | None = None


@app.get("/api/settings")
def get_settings_endpoint() -> dict:
    s = get_settings()
    return {
        "llm_provider": s.llm_provider,
        "ollama_base_url": s.ollama_base_url,
        "deep_think_model": s.deep_think_model,
        "quick_think_model": s.quick_think_model,
        "known_ollama_models": KNOWN_OLLAMA_MODELS,
        "llm_api_key_set": bool(s.llm_api_key),
        "llm_api_key_masked": _mask(s.llm_api_key),
        "telegram_configured": bool(s.telegram_bot_token and s.telegram_chat_id),
        "telegram_bot_token_masked": _mask(s.telegram_bot_token),
        "telegram_chat_id": s.telegram_chat_id,
        "max_concurrent_analyses": s.max_concurrent_analyses,
    }


@app.post("/api/settings")
def update_settings_endpoint(req: SettingsRequest) -> dict:
    update_settings(**req.model_dump(exclude_none=True))
    return get_settings_endpoint()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
