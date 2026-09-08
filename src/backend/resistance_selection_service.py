"""Session-only, causal selection overlay over existing v4 candidates."""
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
from time import perf_counter
from threading import Lock
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from src.backend.swing_book_cursor import SwingBookCursor
from src.market_engine.resistance_selection import select_areas

router = APIRouter(prefix='/api/research/resistance-selection')
_busy = Lock()


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticker: str = Field(pattern=r'^[A-Za-z0-9.\-]{1,20}$')
    session_date: date
    book_id: str
    maximum_width_bps: float = Field(default=100, ge=10, le=300, allow_inf_nan=False)
    minimum_score: float = Field(default=30, ge=0, le=100, allow_inf_nan=False)


def calculate(request):
    started = perf_counter()
    opening = datetime.combine(request.session_date, time(4), ZoneInfo('America/New_York'))
    end = datetime.combine(request.session_date, time(20), ZoneInfo('America/New_York'))
    if end > datetime.now(timezone.utc):
        raise ValueError('Select a completed historical session')
    cursor = SwingBookCursor(request.book_id, request.ticker.upper())
    if cursor.build['version'] != 'causal-swing-closing-book-4':
        raise ValueError('Select a v4 swing book')
    cursor.advance(opening)
    segments, active = [], {}
    # Opening seed and each canonical completed second. No future OHLC is read by selector.
    for stamp in [opening.timestamp(), *(b[0] for b in cursor.bars if opening.timestamp() < b[0] <= end.timestamp())]:
        cursor.advance(datetime.fromtimestamp(stamp, timezone.utc))
        # Departure/retest evidence can change without a geometry revision.
        # Evaluate every completed second; emit only changed areas below.
        areas = select_areas(cursor.engine.active.values(), stamp,
            maximum_width_bps=request.maximum_width_bps, minimum_score=request.minimum_score)
        current = {a['id']: a for a in areas}
        for key in list(active):
            prior = segments[active[key]]
            if key not in current or any(prior[k] != current[key][k] for k in current[key]):
                prior['valid_to'] = stamp
                del active[key]
        for key, area in current.items():
            if key not in active:
                active[key] = len(segments)
                segments.append(dict(area, valid_from=stamp, valid_to=None))
        if len(segments) > 100000:
            raise ValueError('Selection preview exceeds segment budget; no partial result')
    return dict(segments=segments, seconds=perf_counter()-started, book_id=request.book_id,
                fingerprint=cursor.build['fingerprint'], persisted=False,
                contract='resistance-evidence-selection-1')


@router.post('')
def preview(request: SelectionRequest):
    if not _busy.acquire(False):
        raise HTTPException(429, 'A resistance preview is already running')
    try:
        return calculate(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        _busy.release()
