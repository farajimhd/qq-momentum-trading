"""Build offline causal bid marks from the canonical QMD quote stream."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import hashlib
import json
from math import isfinite
import time

from src.market_engine.historical_source import event_from_qmd_payload
from src.market_engine.events import QuoteEvent


class BidMarks:
    def __init__(self, symbol):
        self.symbol = symbol
        self.times, self.prices = [], []
        self.counts = Counter()
        self.last_time = float('-inf')

    def observe(self, payload):
        event = event_from_qmd_payload(payload)
        if not isinstance(event, QuoteEvent) or event.ticker != self.symbol:
            raise ValueError('Bid marks require the requested symbol and quote stream')
        now = event.ts.timestamp()
        if not isfinite(now) or now < self.last_time:
            raise ValueError('Quote marks must arrive in causal timestamp order')
        self.last_time = now
        self.counts['quotes'] += 1
        # Same positive, non-crossed price condition used by runtime execution
        # snapshots. Explicit finiteness also rejects corrupt offline inputs.
        if not all(isfinite(x) for x in (event.bid_price, event.ask_price)):
            self.counts['nonfinite_prices'] += 1
        elif event.bid_price <= 0:
            self.counts['nonpositive_bid'] += 1
        elif event.ask_price < event.bid_price:
            self.counts['crossed_quote'] += 1
        else:
            self.times.append(now)
            self.prices.append(event.bid_price)
            self.counts['accepted'] += 1
            if event.bid_size <= 0:
                self.counts['accepted_without_displayed_bid_size'] += 1


async def run(args):
    from src.backend.qmd_gateway_client import qmd_history_base_url
    from src.market_engine.historical_source import QmdHistoricalEventSource
    from scripts.run_strategy_experiment import save
    content = args.results.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    results = json.loads(content)
    run = results['run']
    if len(run['tickers']) != 1:
        raise ValueError('Require one explicit symbol for bid-mark evaluation')
    symbol = run['tickers'][0]
    if args.output.exists():
        prior = json.loads(args.output.read_text())
        if prior.get('results_sha256') != digest or prior.get('mark_source') != 'bid':
            raise ValueError('Existing bid marks belong to different inputs; use a new output')
        print(f'Skipped completed bid marks | {symbol} | {args.output}')
        return
    source = QmdHistoricalEventSource(qmd_history_base_url(),
        start=datetime.fromisoformat(run['requested_start']), end=datetime.fromisoformat(run['session_end']),
        tickers=[symbol], batch_size=100000, event_kinds=('quote',))
    marks = BidMarks(symbol)
    started = time.monotonic()
    async for rows in source.stream_rows():
        for row in rows:
            marks.observe(row)
        print(f'Active {symbol} bid marks | quotes {marks.counts["quotes"]:,} | '
              f'accepted {marks.counts["accepted"]:,} | elapsed {time.monotonic()-started:.1f}s', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, dict(hindsight_only=True, mark_source='bid', symbol=symbol,
        run_id=run['run_id'], requested_start=run['requested_start'], session_end=run['session_end'],
        results_sha256=digest, source_revision=source.source_revision, counts=dict(marks.counts),
        objective='Offline contemporaneous top-of-book bid valuation; not full-size executable liquidation. Zero-size quotes are counted explicitly.',
        bid_times=marks.times, bid_prices=marks.prices))
    print(f'Completed {symbol} bid marks | {args.output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
