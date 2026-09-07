"""Bounded research-only jobs reading the pinned canonical historical source."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone, timedelta
from math import isfinite
from threading import Lock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.backend.qmd_gateway_client import qmd_history_base_url, qmd_product_request, QmdProductRequest
from src.market_engine.hindsight import HindsightOptimizer, small_profit_filter, merge_positions
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
    max_spread_bps: float = Field(default=100, ge=0, le=10000, allow_inf_nan=False)


def load_macd_intervals(ticker: str, start: datetime, end: datetime, progress, deadline: float) -> tuple[list, list]:
    intervals: list[tuple[float, float]] = []
    provenance = []
    cursor = start
    while cursor < end:
        if monotonic() >= deadline:
            raise RuntimeError("MACD merging exceeded the research time budget; no partial result published")
        chunk_end = min(cursor + timedelta(hours=1), end)
        progress(stage="macd", through=cursor.isoformat())
        payload = qmd_product_request(QmdProductRequest(
            "chart", authority="history", mode="backtest", ticker=ticker, timeframe="1s",
            start=cursor.isoformat(), end=chunk_end.isoformat(), as_of=chunk_end.isoformat(),
            indicator_columns=("bar_start", "bar_end", "macd_line", "macd_signal"),
            stage="bars", include_structure=False, include_market_signals=False,
            limit=50_000, timeout_seconds=min(90, max(1, deadline - monotonic())))).payload
        # One hour cannot exceed 3600 distinct 1s bars. Never accept truncation
        # or an incomplete indicator authority, even if quote reading succeeded.
        evidence = payload.get("indicator_provenance") or {}
        if payload.get("has_more") or not payload.get("indicators_available") or evidence.get("complete") is not True:
            raise RuntimeError("Canonical 1s MACD window is incomplete; merge was not published")
        provenance.append(evidence)
        rows = sorted(payload.get("indicators", []), key=lambda row: row["bar_end"])
        for row in rows:
            timestamp = datetime.fromisoformat(row["bar_end"].replace("Z", "+00:00"))
            if not cursor < timestamp <= chunk_end:
                raise RuntimeError("Canonical MACD bar falls outside its requested closed-bar window")
            line, signal = row.get("macd_line"), row.get("macd_signal")
            if line is None or signal is None or not isfinite(line) or not isfinite(signal):
                continue
            if line > signal:
                t = timestamp.timestamp()
                if intervals and intervals[-1][1] == t:
                    intervals[-1] = (intervals[-1][0], min(t + 1, end.timestamp()))
                else:
                    intervals.append((t, min(t + 1, end.timestamp())))
        cursor = chunk_end
    return intervals, provenance


async def calculate(request: HindsightRequest, progress=lambda **kwargs: None) -> dict:
    start = datetime.combine(request.session_date, time(4), _NY)
    end = datetime.combine(request.session_date, time(20), _NY)
    if end > datetime.now(timezone.utc):
        raise ValueError("Hindsight requires a completed 04:00–20:00 New York session")
    source = QmdHistoricalEventSource(qmd_history_base_url(), start=start, end=end,
                                    tickers=[request.ticker.upper()], batch_size=50_000,
                                    event_kinds=("quote",))
    optimizer = HindsightOptimizer(request.cost_bps, request.max_spread_bps)
    started = monotonic()
    async for batch in source.stream():
        for event in batch.events:
            optimizer.observe(event.ts.timestamp(), event.bid_price, event.ask_price,
                              event.bid_size, event.ask_size)
        progress(quotes=optimizer.quotes, rejected_quotes=optimizer.rejected_quotes,
                 through=batch.events[-1].ts.isoformat(), elapsed_seconds=monotonic() - started)
        if optimizer.quotes > 10_000_000 or monotonic() - started > 600 or (optimizer.path and optimizer.path.count > 50_000):
            raise RuntimeError("Session exceeds the 10-million quote / 50,000-position / 10-minute research budget; no partial optimum published")
    # A certified session with no eligible quotes has zero opportunities, not
    # a data failure. Coverage failures already raise in the source reader.
    result = optimizer.result()
    originals = result["positions"]
    intervals, macd_provenance = await asyncio.to_thread(load_macd_intervals, request.ticker.upper(), start, end, progress, started + 600) if len(originals) > 1 else ([], [])
    merged = merge_positions(originals, intervals, request.cost_bps)
    result.update(positions=merged, position_count=len(merged),
                  unmerged_net_profit_per_share=result["net_profit_per_share"],
                  net_profit_per_share=sum(p["net_profit_per_share"] for p in merged),
                  unmerged_positions=originals, unmerged_position_count=len(originals),
                  merging={"short_gap_seconds": 1, "macd_rule": "completed 1s MACD > signal (including negative values)",
                           "merged_away": len(originals) - len(merged), "macd_provenance": macd_provenance})
    return {**result, "profit_filter": small_profit_filter(result["positions"]),
            "max_spread_bps": request.max_spread_bps, "minimum_displayed_shares_per_side": 1,
            "ticker": request.ticker.upper(), "session_date": str(request.session_date),
            "start": start.isoformat(), "end": end.isoformat(), "cost_bps": request.cost_bps,
            "algorithm": "long-one-share-quote-dp-v3", "hindsight_only": True,
            "objective": "Merged profitable moves from the maximum-net-profit one-share sequence; filtered after merging",
            "execution_assumption": "Observed ask entries / bid exits; at least one share displayed per side; bounded midpoint spread; additional cost per side; no latency, queue or impact model",
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
    key = (request.ticker.upper(), str(request.session_date), request.cost_bps, request.max_spread_bps)
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
