"""Opt-in live v5 continuation, using canonical completed QMD Live seconds.

Bootstrap reads one certified candidate checkpoint. Steady-state requests only
seconds after the last consumed cutoff; no SIP history or strategy I/O per trade.
"""
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
from math import isfinite
import json
from src.backend.experimental_structure_book import resolve, rows
from src.backend.swing_book_source import NY
from src.backend.qmd_gateway_client import qmd_bars, qmd_intraday_bar_history
from src.market_engine.swing_book_v5 import StreamingSwingBookV5, VERSION
from src.backend.swing_book_v5_store import ClosingStore


class LiveSwingBookV5:
    def __init__(self, book_id, ticker, as_of):
        if as_of.tzinfo is None:raise ValueError('Live v5 requires an aware timestamp')
        build=resolve(book_id)
        if build['version']!=VERSION or build['ticker']!=ticker:raise ValueError('Invalid live v5 book')
        self.ticker,self.day=ticker,as_of.astimezone(NY).date()
        self.cutoff=datetime.combine(self.day,time(4),NY)
        source=build['source_book']
        if resolve(source)['fingerprint']!=build['source_fingerprint']:raise ValueError('Live v5 source fingerprint changed')
        self.source=source
        contract=build.get('selection_contract','resistance-evidence-selection-1')
        self.store=ClosingStore(book_id,contract=contract)
        marker=rows(f"SELECT * FROM {source}.sessions FINAL WHERE session_date<'{self.day}' ORDER BY session_date DESC LIMIT 1")
        if not marker:raise ValueError('Live v5 requires a certified prior closing state')
        m=marker[0]
        states=rows(f"SELECT state_json FROM {source}.book FINAL WHERE valid_from_us={int(m['closed_at']*1e6)} ORDER BY level_id")
        seed=dict(version='causal-swing-closing-book-4',closed_at=float(m['closed_at']),sequence=int(m['sequence']),levels=[json.loads(r['state_json']) for r in states])
        if sha256(json.dumps(seed,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=m['state_hash']:raise ValueError('Live v5 seed hash mismatch')
        latest=self.store.latest()
        if latest and seed['closed_at']<latest['closed_at']<self.cutoff.timestamp():seed=latest
        # Refuse a stale seed rather than silently skip intervening market sessions.
        from src.data_provider.calendar import market_sessions
        first_missing=datetime.fromtimestamp(seed['closed_at'],NY).date()+timedelta(days=1)
        last_missing=self.day-timedelta(days=1)
        intervening=market_sessions(first_missing,last_missing) if first_missing<=last_missing else []
        if len(intervening):raise ValueError('Live v5 candidate seed needs historical repair')
        splits=rows(f"SELECT execution_date,split_from,split_to FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker='{ticker}' AND execution_date='{self.day}' AND inserted_at<=parseDateTime64BestEffort('{as_of.isoformat()}')")
        ratios={(float(s['split_from']),float(s['split_to'])) for s in splits}
        if len(ratios)>1:raise ValueError('Conflicting live split ratios')
        factor=next((a/b for a,b in ratios if a>0 and b>0),1.)
        if ratios and any(a<=0 or b<=0 for a,b in ratios):raise ValueError('Invalid split ratio')
        self.engine=StreamingSwingBookV5(seed,self.cutoff.timestamp(),factor,contract=contract)
        self.bootstrapped=False
        self.saved=False

    def finish_session(self, now):
        if now.tzinfo is None:raise ValueError('Live v5 close requires an aware timestamp')
        close=datetime.combine(self.day,time(20),NY)
        if self.saved or now<close:return
        # Catch up any remaining completed bars before committing one closing
        # state. Never persist transient intraday level versions.
        self._consume(self._history(close),close)
        self.cutoff=close
        self.store.save(self.ticker,self.source,self.engine.closing_state(close.timestamp()))
        self.saved=True

    def _history(self, end):
        """One-time paged bootstrap from QMD Live's durable completed bars."""
        before=int(end.timestamp()*1e6)
        result=[]
        for _ in range(8):
            page=qmd_intraday_bar_history(self.ticker,timeframe='1s',start_date=self.day.isoformat(),
                end_date=self.day.isoformat(),before_event_timestamp_us=before,row_limit=10000)
            if page.get('complete') is not True:raise ValueError('Incomplete live bar history')
            result.extend(page.get('bars',[]))
            if not page.get('has_more'):return result
            following=page.get('next_before_event_timestamp_us')
            if not isinstance(following,int) or following>=before:raise ValueError('Invalid live history cursor')
            if hasattr(self,'cutoff') and following<=int(self.cutoff.timestamp()*1e6):return result
            before=following
        raise ValueError('Live history exceeds one-session budget')

    def _consume(self, candidates, end):
        """Validate a complete batch before mutation; duplicate closes must agree."""
        by_time={}
        for bar in candidates:
            if bar.get('is_closed') is False:continue
            if bar.get('sym',self.ticker)!=self.ticker or bar.get('timeframe','1s')!='1s':
                raise ValueError('Live bar identity mismatch')
            stamp=datetime.fromisoformat(str(bar['bar_end']).replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError('Live bar requires timezone')
            if self.cutoff<stamp<=end:
                values=tuple(float(bar[k]) for k in ('high','low','close'))
                high,low,close=values
                if not all(map(isfinite,values)) or not 0<low<=close<=high:
                    raise ValueError('Invalid completed live OHLC')
                if stamp in by_time and by_time[stamp]!=values:
                    raise ValueError('Conflicting completed live candle; reload required')
                by_time[stamp]=values
        for stamp,values in sorted(by_time.items()):self.engine.observe(stamp.timestamp(),*values)

    def snapshot(self, as_of):
        if as_of.tzinfo is None:raise ValueError('Live v5 requires an aware timestamp')
        if as_of.astimezone(NY).date()!=self.day or as_of<self.cutoff:raise ValueError('Live v5 clock changed; reload the session state')
        end=min(as_of.replace(microsecond=0),datetime.combine(self.day,time(20),NY))
        if self.cutoff>=end:return self.engine.snapshot()
        recent=qmd_bars(self.ticker,timeframe='1s',row_limit=500)
        if recent.get('ticker')!=self.ticker or recent.get('timeframe')!='1s':raise ValueError('Live bar identity mismatch')
        history=recent.get('history',[])
        recover=not self.bootstrapped
        # Steady-state must overlap the prior consumed prefix. An empty second
        # is legitimate; a missing retained prefix requires recovery instead.
        if self.bootstrapped and history:
            earliest=min(datetime.fromisoformat(str(b['bar_end']).replace('Z','+00:00')) for b in history)
            if earliest>self.cutoff+timedelta(seconds=1):recover=True
        candidates=self._history(end) if recover else []
        self._consume(candidates+history,end)
        self.cutoff=end
        self.bootstrapped=True
        return self.engine.snapshot()
