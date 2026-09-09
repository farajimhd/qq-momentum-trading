"""Offline trade-marked equity and chronological slice accounting for actual fills."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import hashlib
import json
from datetime import datetime, timezone

import numpy as np


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Equity boundaries and execution times must be timezone aware')
    return parsed.timestamp()


def measure(executions, trade_times, trade_prices, start, end, *, include_end=False):
    """Value [start, end), retaining inventory and cash from earlier fills.

    These are last eligible trade marks, not bid-liquidation estimates. A stale
    mark is retained with its age, never replaced by a future price.
    """
    if not np.isfinite(start) or not np.isfinite(end) or start >= end:
        raise ValueError('Require a finite increasing interval')
    times, prices = np.asarray(trade_times, dtype=float), np.asarray(trade_prices, dtype=float)
    if (times.ndim != 1 or prices.shape != times.shape or not len(times)
            or not np.all(np.isfinite(times)) or not np.all(np.isfinite(prices))
            or np.any(prices <= 0) or np.any(np.diff(times) < 0)):
        raise ValueError('Require ordered finite timestamps and positive eligible trade marks')
    rows = sorted(executions, key=lambda e: stamp(e['source_event_time']))
    fill_times, signed, flows, commissions = [], [], [], []
    identities = set()
    for row in rows:
        if row['execution_id'] in identities:
            raise ValueError('Duplicate execution identity')
        identities.add(row['execution_id'])
        quantity, price, fee = (float(row[k]) for k in ('quantity', 'price', 'commission'))
        if (row['side'] not in ('BUY', 'SELL') or quantity <= 0 or price <= 0
                or not all(np.isfinite(v) for v in (quantity, price, fee))):
            raise ValueError('Invalid execution quantity, side, price or fee')
        change = quantity if row['side'] == 'BUY' else -quantity
        fill_times.append(stamp(row['source_event_time']))
        signed.append(change)
        flows.append(-change * price - fee)
        commissions.append(fee)
    fill_times = np.asarray(fill_times, dtype=float)
    inventory = np.r_[0., np.cumsum(signed)]
    cash = np.r_[0., np.cumsum(flows)]
    fees = np.r_[0., np.cumsum(commissions)]
    if np.any(inventory < -1e-8):
        raise ValueError('This long-only equity audit cannot value net short inventory')

    def values(at, side):
        indices = np.searchsorted(fill_times, at, side=side)
        mark_indices = np.searchsorted(times, at, side=side) - 1
        quantities = inventory[indices]
        if np.any((mark_indices < 0) & (np.abs(quantities) > 1e-8)):
            raise ValueError('Held inventory lacks a causal trade mark')
        marks = prices[np.maximum(mark_indices, 0)]
        return cash[indices] + quantities * marks, quantities, indices, mark_indices

    opening, opening_quantity, first_fill, first_mark = values(start, 'left')
    closing, closing_quantity, last_fill, last_mark = values(end, 'right' if include_end else 'left')
    time_mask = (times >= start) & ((times <= end) if include_end else (times < end))
    fill_mask = (fill_times >= start) & ((fill_times <= end) if include_end else (fill_times < end))
    points = np.unique(np.r_[times[time_mask], fill_times[fill_mask]])
    equity, quantities, _, _ = values(points, 'right')
    curve = np.r_[opening, equity, closing]
    peaks = np.maximum.accumulate(curve)
    drawdowns = peaks - curve
    worst = int(np.argmax(drawdowns))
    clock = np.r_[start, points, end]
    return dict(start=datetime.fromtimestamp(start, timezone.utc).isoformat(),
        end=datetime.fromtimestamp(end, timezone.utc).isoformat(), interval='[start,end]' if include_end else '[start,end)',
        opening_quantity=float(opening_quantity), closing_quantity=float(closing_quantity),
        opening_mark_age_seconds=float(start-times[first_mark]) if first_mark >= 0 else None,
        closing_mark_age_seconds=float(end-times[last_mark]) if last_mark >= 0 else None,
        net_marked_pnl=float(closing-opening), fees=float(fees[last_fill]-fees[first_fill]),
        execution_count=int(last_fill-first_fill), maximum_drawdown=float(drawdowns[worst]),
        drawdown_trough_time=datetime.fromtimestamp(float(clock[worst]), timezone.utc).isoformat(),
        peak_marked_pnl=float(np.max(curve)-opening), minimum_marked_pnl=float(np.min(curve)-opening),
        maximum_inventory=float(np.max(np.r_[opening_quantity, quantities, closing_quantity])),
        valuation_points=int(len(curve)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--benchmark', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--slice', action='append', default=[], metavar='START/END',
        help='Timezone-aware ISO boundaries; include carried inventory and score [start,end)')
    args = parser.parse_args()
    results = json.loads(args.results.read_text())
    benchmark = json.loads(args.benchmark.read_text())
    run = results['run']
    if run['tickers'] != [benchmark['symbol']] or not benchmark.get('hindsight_only'):
        raise ValueError('Require a matching single-symbol canonical benchmark')
    bounds = [(stamp(run['requested_start']), stamp(run['session_end']))]
    if (stamp(benchmark['requested_start']), stamp(benchmark['session_end'])) != bounds[0]:
        raise ValueError('Benchmark coverage must exactly match the run interval')
    for value in args.slice:
        left, right = value.split('/')
        bound = (stamp(left), stamp(right))
        if not bounds[0][0] <= bound[0] < bound[1] <= bounds[0][1]:
            raise ValueError('Slice must lie within the run interval')
        bounds.append(bound)
    reports = [measure(results['executions'], benchmark['eligible_trade_times'],
        benchmark['eligible_trade_prices'], left, right, include_end=index == 0)
        for index, (left, right) in enumerate(bounds)]
    report = dict(run_id=run['run_id'], symbol=benchmark['symbol'],
        source_revision=benchmark['source_revision'],
        limitations='Offline last-eligible-trade equity marks; not executable bid liquidation. Boundary mark ages are explicit. Includes actual fills and commissions; no future mark substitution.',
        input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.results, args.benchmark)},
        full_run=reports[0], slices=reports[1:])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(args.output)
    print(f"Measured equity | {benchmark['symbol']} | slices {len(reports)-1} | {args.output}")


if __name__ == '__main__':
    main()
