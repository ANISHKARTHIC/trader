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
from agent.portfolio.store import list_holdings, upsert_holding, remove_holding

app = FastAPI(title="Trading Agent Dashboard")

STATIC_DIR = Path(__file__).parent / "static"

JobStatus = Literal["queued", "running", "done", "error"]


class Job:
    def __init__(self, job_id: str, symbol: str, analysis_date: str):
        self.id = job_id
        self.symbol = symbol
        self.analysis_date = analysis_date
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


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol.endswith((".NS", ".BO")):
        symbol += ".NS"
    return symbol


def _run_job(job: Job, account_equity: float) -> None:
    job.status = "running"
    try:
        result = run_full_pipeline(
            job.symbol,
            job.analysis_date,
            account_equity=account_equity,
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
    job = Job(job_id, symbol, req.analysis_date)
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job, args=(job, req.account_equity), daemon=True
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
            "status": j.status,
            "created_at": j.created_at,
            "rating": (j.result or {}).get("trade_plan", {}).get("source_rating"),
        }
        for j in jobs
    ]


class TodayJob:
    def __init__(self, job_id: str, analysis_date: str, screen_top_n: int, deep_top_n: int):
        self.id = job_id
        self.analysis_date = analysis_date
        self.screen_top_n = screen_top_n
        self.deep_top_n = deep_top_n
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


def _run_today_job(job: TodayJob, account_equity: float) -> None:
    job.status = "running"

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
        job.status = "done"
    except Exception:
        job.error = traceback.format_exc()
        job.status = "error"


@app.post("/api/today")
def start_today(req: TodayRequest) -> dict:
    job_id = uuid.uuid4().hex[:12]
    analysis_date = req.analysis_date or datetime.now().date().isoformat()
    job = TodayJob(job_id, analysis_date, req.screen_top_n, req.deep_analyze_top_n)
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


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
