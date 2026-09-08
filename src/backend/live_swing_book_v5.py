"""Opt-in live v5 continuation, using canonical completed QMD Live seconds.

Bootstrap reads one certified candidate checkpoint. Steady-state requests only
seconds after the last consumed cutoff; no SIP history or strategy I/O per trade.
"""
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
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
        self.store=ClosingStore(book_id)
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
        self.engine=StreamingSwingBookV5(seed,self.cutoff.timestamp(),factor)
        self.bootstrapped=False
        self.saved=False

    def finish_session(self, now):
        if now.tzinfo is None:raise ValueError('Live v5 close requires an aware timestamp')
        close=datetime.combine(self.day,time(20),NY)
        if self.saved or now<close:return
        # Catch up any remaining completed bars before committing one closing
        # state. Never persist transient intraday level versions.
        for bar in sorted(self._history(close),key=lambda b:b['bar_end']):
            at=datetime.fromisoformat(str(bar['bar_end']).replace('Z','+00:00'))
            if self.cutoff<at<=close:
                self.engine.observe(at.timestamp(),float(bar['high']),float(bar['low']),float(bar['close']))
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
            before=following
        raise ValueError('Live history exceeds one-session budget')

    def snapshot(self, as_of):
        if as_of.tzinfo is None:raise ValueError('Live v5 requires an aware timestamp')
        if as_of.astimezone(NY).date()!=self.day or as_of<self.cutoff:raise ValueError('Live v5 clock changed; reload the session state')
        end=min(as_of.replace(microsecond=0),datetime.combine(self.day,time(20),NY))
        if self.cutoff>=end:return self.engine.snapshot()
        recent=qmd_bars(self.ticker,timeframe='1s',row_limit=500)
        if recent.get('ticker')!=self.ticker or recent.get('timeframe')!='1s':raise ValueError('Live bar identity mismatch')
        history=recent.get('history',[])
        candidates=self._history(end) if not self.bootstrapped else []
        candidates.extend(history)
        # Never consume the forming bar, even when its projected OHLC is present.
        by_time={}
        for bar in candidates:
            if bar.get('is_closed') is False:continue
            stamp=datetime.fromisoformat(str(bar['bar_end']).replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError('Live bar requires timezone')
            if self.cutoff<stamp<=end:
                by_time[stamp]=bar
        # Steady-state must overlap the prior consumed prefix. An empty second
        # is legitimate; a missing retained prefix requires recovery instead.
        if self.bootstrapped and history:
            earliest=min(datetime.fromisoformat(str(b['bar_end']).replace('Z','+00:00')) for b in history)
            if earliest>self.cutoff+timedelta(seconds=1):raise ValueError('Live bar buffer lost continuation; reload required')
        for stamp,bar in sorted(by_time.items()):
            self.engine.observe(stamp.timestamp(),float(bar['high']),float(bar['low']),float(bar['close']))
        self.cutoff=end
        self.bootstrapped=True
        return self.engine.snapshot()
