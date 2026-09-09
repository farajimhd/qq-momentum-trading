"""Mark actual execution inventory across offline hindsight move intervals."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def capture(executions, move):
    start, end = move['entry_time'], move['exit_time']
    opening_quantity = ending_quantity = cash_flow = fees = Decimal(0)
    fills = []
    for execution in executions:
        at = stamp(execution['source_event_time'])
        if at > end:
            continue
        quantity = Decimal(execution['quantity'])
        if execution['side'] not in ('BUY','SELL') or quantity <= 0:
            raise ValueError('Unknown execution side or nonpositive fill quantity')
        signed = quantity if execution['side'] == 'BUY' else -quantity
        ending_quantity += signed
        if at < start:
            opening_quantity += signed
        else:
            cash_flow -= signed*Decimal(execution['price'])
            fees += Decimal(execution.get('commission') or '0')
            fills.append(execution['execution_id'])
    pnl = cash_flow-fees + ending_quantity*Decimal(str(move['exit_price'])) - opening_quantity*Decimal(str(move['entry_price']))
    return dict(move=move, quantity_at_trough=str(opening_quantity), quantity_at_peak=str(ending_quantity),
        interval_cash_flow=str(cash_flow), interval_fees=str(fees), marked_interval_pnl=str(pnl), execution_ids=fills)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',required=True,type=Path)
    parser.add_argument('--benchmark',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--largest',type=int,default=5)
    args = parser.parse_args()
    if args.largest <= 0:
        parser.error('--largest must be positive')
    results = json.loads(args.results.read_text(encoding='utf-8'))
    benchmark = json.loads(args.benchmark.read_text(encoding='utf-8'))
    run = results['run']
    if run['tickers'] != [benchmark['symbol']] or not benchmark.get('hindsight_only'):
        raise ValueError('Require the matching single-symbol hindsight benchmark')
    start, end = stamp(run['requested_start']),stamp(run['session_end'])
    moves = sorted((m for m in benchmark['benchmark']['positions'] if m['direction']=='long'
                    and start <= m['entry_time'] < m['exit_time'] <= end),
                   key=lambda m:m['gross_return_bps'],reverse=True)[:args.largest]
    if not moves:
        raise ValueError('No benchmark moves overlap this run window; do not substitute another date')
    report = dict(run_id=run['run_id'],symbol=benchmark['symbol'],source_revision=benchmark['source_revision'],
        limitations='Offline price marks, not executable liquidation returns or forward signals. Interval P&L includes actual fills and fees plus inventory marked at benchmark extrema. It can include inventory acquired before the interval.',
        input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.results,args.benchmark)},
        moves=[capture(results['executions'],m) for m in moves])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,indent=2),encoding='utf-8')
    temporary.replace(args.output)
    print(f"Measured {len(moves)} offline moves | {benchmark['symbol']} | {args.output}")


if __name__ == '__main__':
    main()
