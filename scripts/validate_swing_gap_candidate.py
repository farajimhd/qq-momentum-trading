"""Restartable two-case research validation using the real Backtest authority."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.request import Request, urlopen


def api(path, body=None):
    request = Request('http://127.0.0.1:8000'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def save(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def audit(run_id):
    journal = Path('D:/TradingML/runtimes/trading/backtest')/run_id/'journal.sqlite3'
    reasons, future, entries = Counter(), [], []
    with sqlite3.connect(journal.as_uri()+'?mode=ro', uri=True) as connection:
        for timestamp, raw in connection.execute("SELECT event_time,payload_json FROM journal WHERE category='strategy_decision' ORDER BY sequence"):
            decision = json.loads(raw)
            reasons[decision.get('reason', '')] += 1
            if decision.get('action') != 'enter_long':
                continue
            available = datetime.fromisoformat(timestamp).timestamp()*1000
            metadata = decision.get('metadata', {})
            def check(value):
                if isinstance(value, dict):
                    if 'confirmed_at_ms' in value:
                        for name in ('created_at_ms', 'confirmed_at_ms', 'formed_at_ms', 'last_role_change_at_ms'):
                            stamp = value.get(name)
                            if isinstance(stamp, (int, float)) and stamp > available:
                                future.append(dict(decision_at=timestamp, field=name, value=stamp))
                    for child in value.values():
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)
            check(metadata)
            entries.append(dict(at=timestamp, reason=decision.get('reason'),
                reference_price=metadata.get('reference_price'), stop=decision.get('invalidation_price'),
                selection=metadata.get('profit_target_selection') or metadata.get('gap_selection'),
                support=metadata.get('protective_stop_selection')))
        terminal = connection.execute("SELECT payload_json FROM journal WHERE category='snapshot' AND entity_type='portfolio' ORDER BY sequence DESC LIMIT 1").fetchone()
        equity = json.loads(terminal[0])['netliquidation']['amount'] if terminal else None
    return dict(decision_reasons=dict(reasons), future_level_violations=future, entry_decisions=entries,
                terminal_equity=equity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    root = Path('D:/TradingML/runtimes').resolve()
    output = args.output.resolve()
    if not root.is_dir() or not output.is_relative_to(root):
        parser.error('Output must be under the available laptop runtime root')
    output.mkdir(parents=True, exist_ok=True)
    path = output/'experiment.json'
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    state = json.loads(path.read_text()) if path.exists() else dict(
        candidate=args.candidate, plan=args.plan, created_at=datetime.now(timezone.utc).isoformat(),
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(), cases=cases, runs={})
    if state['candidate'] != args.candidate or state['plan'] != args.plan or state['cases'] != cases:
        raise ValueError('Existing experiment identity differs; never overwrite it')
    for case in cases:
        key = case['name']
        if key in state['runs']:
            continue
        body = {k: v for k, v in case.items() if k != 'name'}
        body.update(configuration_revision_id=args.candidate, run_plan_id=args.plan)
        print(f'Creating {key}', flush=True)
        run = api('/api/trading/backtest/runs', body)
        state['runs'][key] = dict(run_id=run['run_id'], status=run['status'])
        save(path, state)
    deadline = time.monotonic()+3600
    while True:
        pending = 0
        for key, run in state['runs'].items():
            if run.get('validated'):
                continue
            snapshot = api('/api/trading/backtest/runs/'+run['run_id'])
            status = snapshot['status']
            progress = int(float(snapshot.get('progress') or 0)*10)
            if (status, progress) != (run.get('status'), run.get('progress_bucket')):
                print(f'{key}: {status}, {progress*10}% of session', flush=True)
            run.update(status=status, progress_bucket=progress, error=snapshot.get('error',''))
            if status in ('failed', 'stopped'):
                save(path, state)
                raise RuntimeError(f"{key}: {status}: {run['error']}; preserve evidence and diagnose")
            if status != 'completed':
                pending += 1
                continue
            symbol = next(c['tickers'][0] for c in cases if c['name']==key)
            result = api('/api/trading/backtest/runs/'+run['run_id']+'/results?symbol='+symbol)
            trades = result.get('closed_trades', [])
            closed_net = sum((Decimal(str(t['net_pnl'])) for t in trades), Decimal(0))
            listed = next(r for r in api('/api/trading/backtest/runs')['rows'] if r['run_id']==run['run_id'])
            evidence = audit(run['run_id'])
            initial = next(c['initial_cash'] for c in cases if c['name']==key)
            net = Decimal(str(evidence['terminal_equity']))-Decimal(str(initial))
            if abs(net-Decimal(str(listed['net_pnl']))) > Decimal('.0001'):
                raise RuntimeError('Terminal journal equity differs from canonical run result')
            save(output/(key+'-audit.json'), evidence)
            if evidence['future_level_violations']:
                raise RuntimeError('Future level evidence found; candidate must not be accepted')
            run.update(validated=True, net_pnl=str(net), closed_net_pnl=str(closed_net), closed_trades=trades,
                       entry_decision_count=len(evidence['entry_decisions']))
            print(f'{key}: completed; {len(trades)} closed trades; equity net ${net:.2f}; closed net ${closed_net:.2f}; causal entry audit passed', flush=True)
        save(path, state)
        if not pending:
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Validation exceeded one hour; runs preserved for diagnosis')
        time.sleep(10)
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        comparison = {}
        for key, run in state['runs'].items():
            original = baseline['runs'][key]
            wins = [t for t in original['closed_trades'] if Decimal(t['net_pnl'])>0]
            matched = []
            for trade in wins:
                start, end = (datetime.fromisoformat(trade[k]) for k in ('opened_at','closed_at'))
                overlap = [t for t in run['closed_trades'] if datetime.fromisoformat(t['opened_at']) < end
                           and datetime.fromisoformat(t['closed_at']) > start]
                matched.append(dict(baseline_trade=trade.get('trade_id'), baseline_open=trade['opened_at'],
                    baseline_net=trade['net_pnl'], overlap_count=len(overlap),
                    overlapping_net=str(sum((Decimal(t['net_pnl']) for t in overlap), Decimal(0)))))
            comparison[key] = dict(net_change=str(Decimal(run['net_pnl'])-Decimal(original['net_pnl'])), winning_trade_coverage=matched)
        state['comparison'] = comparison
        save(path, state)
    print(f'Validated experiment: {path}', flush=True)


if __name__ == '__main__':
    main()
