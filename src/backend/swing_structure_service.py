"""Read-only, on-demand session preview; never writes structural checkpoints."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from time import perf_counter
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.backend.qmd_gateway_client import QmdProductRequest, qmd_product_request, qmd_historical_source_revision
from src.market_engine.swing_structure import SwingSettings, SwingStructure

router = APIRouter(prefix='/api/research/swing-structure', tags=['swing structure prototype'])
_busy = Lock()
_NY = ZoneInfo('America/New_York')


class SwingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticker: str = Field(min_length=1, max_length=20, pattern=r'^[A-Za-z0-9.\-]+$')
    session_date: date
    reversal_bps: float = Field(default=50, ge=10, le=500, allow_inf_nan=False)
    volatility_multiple: float = Field(default=2, ge=.5, le=6, allow_inf_nan=False)
    major_multiple: float = Field(default=3, ge=1, le=6, allow_inf_nan=False)
    volatility_cap_multiple: float = Field(default=2, ge=1, le=4, allow_inf_nan=False)


def calculate(request):
    start = datetime.combine(request.session_date, time(4), _NY)
    end = datetime.combine(request.session_date, time(20), _NY)
    if end > datetime.now(timezone.utc):
        raise ValueError('Select a completed New York session')
    ticker = request.ticker.upper()
    started = perf_counter()
    revision_args = dict(start=start.isoformat(), end=end.isoformat(), tickers=[ticker])
    revision = qmd_historical_source_revision(**revision_args)

    def read(i):
        left = start+timedelta(hours=i*4)
        right = left+timedelta(hours=4)
        response = qmd_product_request(QmdProductRequest('chart', authority='history', mode='backtest',
            ticker=ticker, timeframe='1s', start=left.isoformat(), end=right.isoformat(), as_of=right.isoformat(),
            stage='bars', indicator_columns=('bar_start', 'bar_end', 'close'),
            include_structure=False, include_market_signals=False, limit=50000, timeout_seconds=90))
        payload = response.payload
        source = (payload.get('cache') or {}).get('source_revision') or payload.get('source_revision') or {}
        if payload.get('has_more') or source.get('complete_for_history') is not True or source.get('request_complete') is not True:
            raise ValueError('Canonical bar window is incomplete; no partial structure published')
        rows = payload.get('bars', [])
        if len(rows) > 14400:
            raise ValueError('Canonical one-second window exceeded its row bound')
        return left.timestamp(), right.timestamp(), rows

    engine = SwingStructure(SwingSettings(reversal_bps=request.reversal_bps,
        volatility_multiple=request.volatility_multiple, major_multiple=request.major_multiple,
        volatility_cap_multiple=request.volatility_cap_multiple))
    compute_seconds = 0.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='swing-preview-bars') as readers:
        for left, right, rows in readers.map(read, range(4)):
            compute_start = perf_counter()
            for row in rows:
                t = datetime.fromisoformat(row['bar_end'].replace('Z', '+00:00')).timestamp()
                if not left < t <= right:
                    raise ValueError('Canonical bar outside its requested window')
                if row.get('event_count') == 0:
                    engine.counts['empty_bars'] += 1
                    continue
                engine.observe(t, float(row['high']), float(row['low']), float(row['close']))
            compute_seconds += perf_counter()-compute_start
    if qmd_historical_source_revision(**revision_args)['token'] != revision['token']:
        raise ValueError('Canonical source changed during preview; retry')
    result = engine.result()
    return {**result, 'ticker': ticker, 'session_date': str(request.session_date), 'session_end': end.timestamp(),
            'source_revision': revision, 'timing': {'total_seconds': perf_counter()-started, 'compute_seconds': compute_seconds},
            'persisted': False, 'scope': 'session-only; no prior-session anchors; not used by strategy'}


@router.post('')
def preview(request: SwingRequest):
    if not _busy.acquire(blocking=False):
        raise HTTPException(429, 'A swing preview is already running; retry when it finishes')
    try:
        return calculate(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        _busy.release()
