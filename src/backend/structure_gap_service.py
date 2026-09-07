"""On-demand read-only v4 gap preview; no strategy or checkpoint writes."""
from datetime import datetime,time,timezone
from zoneinfo import ZoneInfo
from collections import deque
from statistics import median
from threading import Lock
from time import perf_counter
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from datetime import date
from src.backend.swing_book_cursor import SwingBookCursor
from src.market_engine.structure_gaps import GapAnalyzer

router=APIRouter(prefix='/api/research/structure-gaps',tags=['structure gaps'])
_busy=Lock()

class GapRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ticker:str=Field(pattern=r'^[A-Za-z0-9.\-]{1,20}$')
    session_date:date
    book_id:str
    minimum_p_norm:float=Field(default=.2,ge=0,le=1,allow_inf_nan=False)
    proximity_bps:float=Field(default=50,ge=5,le=300,allow_inf_nan=False)
    cost_bps:float=Field(default=20,ge=0,le=200,allow_inf_nan=False)
    minimum_gap_bps:float=Field(default=20,ge=0,le=500,allow_inf_nan=False)
    maximum_gaps:int=Field(default=3,ge=1,le=10)

def calculate(request):
    started=perf_counter();ny=ZoneInfo('America/New_York')
    opening=datetime.combine(request.session_date,time(4),ny)
    end=datetime.combine(request.session_date,time(20),ny)
    if end>datetime.now(timezone.utc):raise ValueError('Select a completed historical session')
    cursor=SwingBookCursor(request.book_id,request.ticker.upper(),normalized=True)
    if cursor.build['version']!='causal-swing-closing-book-4':raise ValueError('Select a v4 swing book')
    cursor.advance(opening)
    engine=GapAnalyzer(request.proximity_bps,request.cost_bps,request.maximum_gaps,request.minimum_gap_bps)
    ranges=deque(maxlen=30);previous=None
    for t,high,low,close in cursor.bars:
        if t>end.timestamp():break
        noise=median(ranges) if ranges else max(.01,high-low)
        snap=cursor.snapshot(datetime.fromtimestamp(t,timezone.utc))
        levels=[l for l in snap['unified_levels'] if l.get('p_norm') is not None and l['p_norm']>=request.minimum_p_norm]
        before=len(engine.setups)
        engine.observe(t,high,low,close,levels,noise)
        for setup in engine.setups[before:]:
            members=[l for l in cursor.engine.active.values() if l['side']=='support' and l['state']=='active' and l['scale']=='major'
                     and setup['support_lower']<=l['price']<=setup['support']]
            setup['prior_retests']=max((l.get('independent_retests',0) for l in members),default=0)
        ranges.append(max(high-low,abs(high-previous),abs(low-previous)) if previous is not None else high-low)
        previous=close
    return dict(segments=engine.segments,setups=engine.setups,session_end=end.timestamp(),
                book_id=request.book_id,fingerprint=cursor.build['fingerprint'],settings=request.model_dump(mode='json'),
                seconds=perf_counter()-started,score_contract='100 * net_upside / (net_upside + risk_with_cost + prior_noise)',
                persisted=False)

@router.post('')
def preview(request:GapRequest):
    if not _busy.acquire(False):raise HTTPException(429,'A gap preview is already running')
    try:return calculate(request)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    except Exception as exc:raise HTTPException(502,str(exc)) from exc
    finally:_busy.release()
