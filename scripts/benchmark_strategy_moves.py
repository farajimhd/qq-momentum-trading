"""Offline price-only hindsight labels and exact-trade excursions for a run window."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from bisect import bisect_left, bisect_right
from datetime import datetime
import json
import sqlite3
import time


async def run(args):
    from src.backend.qmd_gateway_client import qmd_history_base_url
    from src.market_engine.historical_source import QmdHistoricalEventSource
    from src.market_engine.hindsight import PriceMacdLabels
    from scripts.run_strategy_experiment import save
    from scripts.audit_strategy_positions import timestamp
    results = json.loads(args.results.read_text())
    audit = json.loads(args.audit.read_text())
    run = results['run']
    source = QmdHistoricalEventSource(qmd_history_base_url(),
        start=datetime.fromisoformat(run['requested_start']), end=datetime.fromisoformat(run['session_end']),
        tickers=[audit['symbol']], batch_size=100000, event_kinds=('trade',))
    labels = PriceMacdLabels()
    started = time.monotonic()
    async for rows in source.stream_rows():
        for row in rows:
            labels.observe_payload(row)
        print(f"Reading {audit['symbol']} | canonical trades {labels.trades:,} | elapsed {time.monotonic()-started:.1f}s", flush=True)
    intervals = []
    opened = direction = None
    with sqlite3.connect(Path(audit['frame_cache']).as_uri()+'?mode=ro', uri=True) as connection:
        for at, raw in connection.execute('select as_of,indicator_json from strategy_frames order by as_of_us'):
            t = timestamp(at)
            if t < timestamp(run['requested_start']):
                continue
            values = json.loads(raw)
            line, signal = values.get('macd_line'), values.get('macd_signal')
            new = ('long' if line > signal else 'short' if line < signal else None) if line is not None and signal is not None else None
            if new != direction:
                if opened is not None and t > opened:
                    intervals.append((opened,t,direction))
                opened, direction = (t if new else None), new
    if opened is not None and opened < timestamp(run['session_end']):
        intervals.append((opened,timestamp(run['session_end']),direction))
    benchmark = labels.result(intervals)
    excursions = []
    for p in results['position_lifecycles']:
        start, end = timestamp(p['opened_at']), timestamp(p['closed_at'] or run['session_end'])
        first, last = bisect_left(labels.times,start), bisect_right(labels.times,end)
        prices = labels.prices[first:last]
        entry = float(p['entry_price'])
        excursions.append(dict(lifecycle_id=p['lifecycle_id'], eligible_trades=len(prices),
            mfe_bps=(max(prices)/entry-1)*10000 if prices else None,
            mae_bps=(min(prices)/entry-1)*10000 if prices else None))
    save(args.output, dict(hindsight_only=True, run_id=run['run_id'], symbol=audit['symbol'],
        requested_start=run['requested_start'], session_end=run['session_end'],
        objective='Price-only MACD direction episode extrema; not executable profit',
        source_revision=source.source_revision, benchmark=benchmark, excursions=excursions,
        eligible_trade_times=list(labels.times), eligible_trade_prices=list(labels.prices)))
    print(f"Benchmark complete | episodes {len(intervals)} | labels {benchmark['position_count']} | {args.output}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('results','audit','output'):
        p.add_argument('--'+name,type=Path,required=True)
    asyncio.run(run(p.parse_args()))


if __name__ == '__main__':
    main()
