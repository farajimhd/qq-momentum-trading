"""Read a closing seed, then reconstruct only the requested causal prefix."""
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import RLock
from bisect import bisect_right
from copy import deepcopy
import json

from src.market_engine.swing_book import VERSION, SwingBook, project
from src.trading_runtime.normalized_level_book import calibration, transform, CONTRACT
from .swing_book_source import read_session, session_bounds, NY


@lru_cache(maxsize=4)
def inputs(build_id, ticker, session, fingerprint):
    from .experimental_structure_book import resolve, rows
    build = resolve(build_id)
    if build['ticker']!=ticker or build['fingerprint']!=fingerprint:
        raise ValueError('Swing book identity changed')
    if build['version']=='causal-swing-closing-book-5':
        return inputs(build['source_book'],ticker,session,build['source_fingerprint'])
    manifest = json.loads((Path(build['runtime'])/'source_manifest.json').read_text())
    dates = [r['source_date'] for r in manifest[0]]
    if session not in dates:
        raise ValueError('Session is outside the certified swing book')
    opening, _ = session_bounds(session)
    markers = rows(f"SELECT * FROM {build_id}.sessions FINAL WHERE session_date<'{session}' ORDER BY session_date DESC LIMIT 1")
    seed, previous_close = None, None
    if markers:
        marker = markers[0]
        source = rows(f"SELECT state_json FROM {build_id}.book FINAL WHERE valid_from_us={int(marker['closed_at']*1000000)} ORDER BY level_id")
        seed = dict(version=build['version'],closed_at=float(marker['closed_at']),sequence=int(marker['sequence']),
                    levels=[json.loads(r['state_json']) for r in source])
        from hashlib import sha256
        digest = sha256(json.dumps(seed,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest!=marker['state_hash']:
            raise ValueError('Closing seed integrity check failed')
        previous_close = float(marker['close'])
    factor = 1.
    if seed:
        for split in manifest[2]:
            effective = session_bounds(split['execution_date'])[0].timestamp()
            if seed['closed_at']<effective<=opening.timestamp():
                factor *= float(split['split_from'])/float(split['split_to'])
    bars, revision = read_session(ticker,session,policy=build.get('source_policy','canonical-causal-ohlc-1'))
    expected = rows(f"SELECT source_revision FROM {build_id}.sessions FINAL WHERE session_date='{session}'")
    if not expected or json.loads(expected[0]['source_revision'])['token']!=revision['token']:
        raise ValueError('Canonical session changed since book construction; rebuild required')
    return seed, previous_close, factor, bars


class SwingBookCursor:
    def __init__(self, build_id, ticker, fingerprint=None, *, normalized=False):
        from .experimental_structure_book import resolve
        self.build = resolve(build_id)
        if fingerprint is not None and fingerprint!=self.build['fingerprint']:
            raise ValueError('Book fingerprint changed')
        self.ticker, self.normalized = ticker, normalized
        self.lock, self.session, self.at = RLock(), None, -1

    def advance(self, cutoff):
        if cutoff.tzinfo is None:
            raise ValueError('Timezone-aware cutoff required')
        session, stamp = cutoff.astimezone(NY).date().isoformat(), cutoff.timestamp()
        with self.lock:
            if session!=self.session or stamp<self.at:
                seed, close, self.factor, self.bars = inputs(self.build['id'],self.ticker,session,self.build['fingerprint'])
                opening, _ = session_bounds(session)
                if self.build['version']=='causal-swing-closing-book-5':
                    from src.market_engine.swing_book_v5 import StreamingSwingBookV5
                    self.engine = StreamingSwingBookV5(seed,opening.timestamp(),self.factor)
                else:
                    self.engine = SwingBook(seed,opening.timestamp(),self.factor,version=self.build['version'])
                self.index, self.session = 0, session
                self.basis = calibration(self.engine.snapshot()['unified_levels'],close*self.factor) if close and close>0 and self.build['version']!='causal-swing-closing-book-5' else None
                if self.basis is not None:
                    self.basis.update(frozen_at=opening.isoformat(),
                        prior_session=datetime.fromtimestamp(seed['closed_at'],NY).date().isoformat(),
                        close_authority='last causal completed regular-session second')
                self.cached_key = None
            while self.index<len(self.bars) and self.bars[self.index][0]<=stamp:
                self.engine.observe(*self.bars[self.index])
                self.index += 1
            self.at = stamp

    def snapshot(self, cutoff, sequence=None):
        # Completed bars at t include executions before t, never executions at t.
        with self.lock:
            self.advance(cutoff)
            key = (self.session,self.engine.revision)
            if self.cached_key!=key:
                raw = self.engine.snapshot()
                if self.normalized and self.build['version']!='causal-swing-closing-book-5':
                    if self.basis is None:
                        raise ValueError('Normalized swing book requires a certified preceding regular-session close')
                    raw = transform(raw['unified_levels'],self.basis)
                self.cached_value, self.cached_key = raw, key
            return self.cached_value


class SwingChartTimeline:
    """Each second is evaluated once; rewinds only slice the retained prefix."""
    def __init__(self,build_id,ticker,start,fingerprint,contract=CONTRACT):
        self.start,self.contract = start,contract
        self.cursor = SwingBookCursor(build_id,ticker,fingerprint,normalized=True)
        self.lock,self.initialized = RLock(),False
        self.output,self.output_stamps = [],[]
        self.previous,self.last_revision = None,None
        self.next_index,self.bytes,self.transitions = 0,0,0

    def rows(self,end,after=None):
        if end<self.start or self.start.astimezone(NY).date()!=end.astimezone(NY).date():
            raise ValueError('Swing chart requires a single session')
        with self.lock:
            if not self.initialized:
                self.cursor.advance(self.start)
                if self.cursor.basis:
                    self.cursor.basis['merge_contract'] = self.contract
                self.stamps = [self.start.timestamp()]+[b[0] for b in self.cursor.bars if b[0]>self.start.timestamp()]
                self.initialized = True
            stop = bisect_right(self.stamps,end.timestamp())
            while self.next_index<stop:
                stamp = self.stamps[self.next_index]
                at = datetime.fromtimestamp(stamp,timezone.utc)
                self.cursor.advance(at)
                self.next_index += 1
                self.transitions += 1
                if self.last_revision==self.cursor.engine.revision:
                    continue
                self.last_revision = self.cursor.engine.revision
                current = {r['unified_level_id']:r for r in self.cursor.snapshot(at)['unified_levels']}
                row = dict(bar_start=at.isoformat(),bar_end=at.isoformat())
                if self.previous is None:
                    row['qmd_structure_unified_levels'] = list(current.values())
                else:
                    upserts = [r for k,r in current.items() if self.previous.get(k)!=r]
                    removed = [dict(unified_level_id=k,side=r['side']) for k,r in self.previous.items() if k not in current]
                    if upserts or removed:
                        row['qmd_structure_unified_level_delta'] = dict(upserts=upserts,removed=removed)
                if len(row)>2:
                    self.output.append(row)
                    self.output_stamps.append(stamp)
                    self.bytes += len(json.dumps(row,separators=(',',':')))
                self.previous = current
            begin = bisect_right(self.output_stamps,after.timestamp()) if after else 0
            return deepcopy(self.output[begin:bisect_right(self.output_stamps,end.timestamp())])
