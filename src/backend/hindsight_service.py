"""Bounded research-only jobs reading the pinned canonical historical source."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone
from threading import Lock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.backend.qmd_gateway_client import qmd_history_base_url
from src.market_engine.hindsight import HindsightOptimizer
from src.market_engine.historical_source import QmdHistoricalEventSource

router = APIRouter(prefix="/api/research/hindsight", tags=["hindsight research"])
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight-research")
_lock = Lock()
_jobs: dict[str, dict] = {}
_NY = ZoneInfo("America/New_York")


class HindsightRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    session_date: date
    cost_bps: float = Field(default=5, ge=0, le=1000, allow_inf_nan=False)


async def calculate(request: HindsightRequest, progress=lambda **kwargs: None) -> dict:
    start = datetime.combine(request.session_date, time(4), _NY)
    end = datetime.combine(request.session_date, time(20), _NY)
    if end > datetime.now(timezone.utc):
        raise ValueError("Hindsight requires a completed 04:00–20:00 New York session")
    source = QmdHistoricalEventSource(qmd_history_base_url(), start=start, end=end,
                                    tickers=[request.ticker.upper()], batch_size=50_000,
                                    event_kinds=("quote",))
    optimizer = HindsightOptimizer(request.cost_bps)
    started = monotonic()
    async for batch in source.stream():
        for event in batch.events:
            optimizer.observe(event.ts.timestamp(), event.bid_price, event.ask_price,
                              event.bid_size, event.ask_size)
        progress(quotes=optimizer.quotes, rejected_quotes=optimizer.rejected_quotes,
                 through=batch.events[-1].ts.isoformat(), elapsed_seconds=monotonic() - started)
        if optimizer.quotes > 10_000_000 or monotonic() - started > 600 or (optimizer.path and optimizer.path.count > 50_000):
            raise RuntimeError("Session exceeds the 10-million quote / 50,000-position / 10-minute research budget; no partial optimum published")
    if optimizer.quotes == optimizer.rejected_quotes:
        raise ValueError("No valid two-sided quotes in the certified session")
    return {**optimizer.result(), "ticker": request.ticker.upper(), "session_date": str(request.session_date),
            "start": start.isoformat(), "end": end.isoformat(), "cost_bps": request.cost_bps,
            "algorithm": "long-one-share-quote-dp-v1", "hindsight_only": True,
            "objective": "Maximum total net profit per share; one long position at a time",
            "execution_assumption": "Observed ask entries / bid exits; additional cost per side; no latency, queue or impact model",
            "source_revision": source.source_revision, "elapsed_seconds": monotonic() - started}


def _run(job_id: str, request: HindsightRequest):
    def update(**kwargs):
        with _lock:
            _jobs[job_id].update(kwargs)
    update(status="running")
    try:
        result = asyncio.run(calculate(request, update))
        update(status="completed", result=result)
    except Exception as exc:
        update(status="failed", error=str(exc))


@router.post("")
def start_hindsight(request: HindsightRequest):
    end = datetime.combine(request.session_date, time(20), _NY)
    if end > datetime.now(timezone.utc):
        raise HTTPException(422, "Select a completed historical session")
    key = (request.ticker.upper(), str(request.session_date), request.cost_bps)
    with _lock:
        # Only deduplicate active requests. Re-running revalidates source revision.
        for job in _jobs.values():
            if job["key"] == key and job["status"] in {"queued", "running"}:
                return {k: v for k, v in job.items() if k != "key"}
        if sum(j["status"] in {"queued", "running"} for j in _jobs.values()) >= 3:
            raise HTTPException(429, "Hindsight queue is full; retry when another session completes")
        while len(_jobs) >= 8:
            completed = next((k for k, j in _jobs.items() if j["status"] in {"completed", "failed"}), None)
            if completed is None:
                raise HTTPException(429, "Hindsight queue is full")
            del _jobs[completed]
        job_id = str(uuid4())
        job = {"id": job_id, "key": key, "status": "queued", "quotes": 0}
        _jobs[job_id] = job
        response = {k: v for k, v in job.items() if k != "key"}
        _pool.submit(_run, job_id, request)
        return response


@router.get("/{job_id}")
def hindsight_status(job_id: str):
    with _lock:
        if job_id not in _jobs:
            raise HTTPException(404, "Hindsight job expired; generate it again")
        return {k: v for k, v in _jobs[job_id].items() if k != "key"}
