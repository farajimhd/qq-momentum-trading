#!/usr/bin/env python3
"""Read-only v5 SQL parity and streaming latency audit on representative sessions."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime
import json
from time import perf_counter
import numpy as np
from research.mlops.env import discover_env_files,load_env_files
from src.backend.experimental_structure_book import resolve,rows
from src.backend.swing_book_cursor import inputs
from src.backend.swing_book_source import session_bounds
from src.market_engine.swing_book_v5 import StreamingSwingBookV5
from src.market_engine.resistance_selection import select_areas


def run(book,session):
    b=resolve(book);source=b['source_book']
    dates=rows(f'SELECT DISTINCT valid_from_us FROM {source}.book FINAL ORDER BY valid_from_us')
    checked=0
    for offset in range(0,len(dates),10):
        stamps=[int(r['valid_from_us']) for r in dates[offset:offset+10]]
        fetched=rows(f"SELECT valid_from_us,state_json FROM {source}.book FINAL WHERE valid_from_us IN ({','.join(map(str,stamps))})")
        for stamp in stamps:
            expected={a['id']:a for a in select_areas([json.loads(r['state_json']) for r in fetched if r['valid_from_us']==stamp],stamp/1e6) if a['selected']}
            actual=rows(f"SELECT * FROM {book}.book FINAL WHERE side=-1 AND valid_from_us<={stamp} AND (valid_to_us IS NULL OR valid_to_us>{stamp})")
            assert set(expected)=={r['level_id'][2:] for r in actual},f'Membership mismatch at {stamp}'
            for r in actual:
                a=expected[r['level_id'][2:]]
                assert all(abs(r[k]-a[k])<1e-8 for k in ('price','lower','upper'))
                assert abs(r['selection_score']-a['score'])<1e-8
            checked+=1
    before=perf_counter()
    seed,close,factor,bars=inputs(book,b['ticker'],session,b['fingerprint'])
    load_seconds=perf_counter()-before
    opening,_=session_bounds(session)
    engine=StreamingSwingBookV5(seed,opening.timestamp(),factor)
    durations=[];max_levels=0
    end=datetime.fromisoformat(session+('T07:30:00-04:00' if b['ticker']=='JUNS' else 'T04:30:00-04:00')).timestamp()
    # The session ranges are validation fixtures, never algorithm parameters.
    for bar in bars:
        if bar[0]>end:break
        before=perf_counter();engine.observe(*bar);snapshot=engine.snapshot()
        durations.append((perf_counter()-before)*1000)
        max_levels=max(max_levels,len(snapshot['unified_levels']))
        assert all(max(r['created_at_ms'],r['confirmed_at_ms'])<=bar[0]*1000 for r in snapshot['unified_levels'])
    return dict(ticker=b['ticker'],book=book,closing_states_checked=checked,session=session,
        cold_input_seconds=load_seconds,bars=len(durations),streaming_total_seconds=sum(durations)/1000,
        update_plus_snapshot_ms=dict(zip(('p50','p95','p99','maximum'),map(float,np.percentile(durations,[50,95,99,100])))),
        maximum_visible_levels=max_levels,status='passed')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--books',nargs='+',required=True)
    p.add_argument('--session',default='2026-08-21');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not a.output.resolve().is_relative_to(Path(r'D:\TradingML\runtimes')):raise ValueError('Use required runtime root')
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    results=[]
    for book in a.books:
        print(f'{book}: validating all closing states and streaming session',flush=True)
        results.append(run(book,a.session));a.output.write_text(json.dumps(results,indent=2))
        print(f'{results[-1]["ticker"]}: passed | {results[-1]["closing_states_checked"]} closes | {results[-1]["bars"]} bars',flush=True)

if __name__=='__main__':main()
