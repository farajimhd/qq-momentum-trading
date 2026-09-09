"""Summarize every recorded experiment and position without selecting a winner."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True, type=Path)
    args = parser.parse_args()
    root = args.runtime_root.resolve()
    if not root.is_dir():
        parser.error('Runtime root does not exist')
    rows, positions = [], []
    counts = dict(active=0, completed=0, failed=0, other=0)
    for path in sorted(root.glob('*/ledger.json')):
        ledger = json.loads(path.read_text(encoding='utf-8'))
        for case in ledger.get('cases', {}).values():
            status = case.get('status', 'other')
            counts[status if status in counts else 'other'] += 1
            row = dict(experiment=path.parent.name, identity=ledger['identity'], **case)
            rows.append(row)
            audit = path.parent/f"{case['symbol']}-audit.json"
            if status != 'completed' or not audit.exists():
                continue
            data = json.loads(audit.read_text(encoding='utf-8'))
            if data['run_id'] != case['run_id']:
                raise ValueError(f'Stale audit: {audit}')
            for p in data['positions']:
                positions.append(dict(experiment=path.parent.name, symbol=case['symbol'],
                    run_id=case['run_id'], **{key:p.get(key) for key in (
                        'number','lifecycle_id','opened_at','closed_at','entry_price','exit_price',
                        'quantity','net_pnl','fees','duration_seconds','exit_reason','entry_gate_passed',
                        'acquired_fraction_of_approved','approved_quantity_exceeded','diagnostic_flags')}))
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), counts=counts,
                  experiments=rows, positions=positions)
    lines = ['# Strategy research ledger', '',
        'All candidates are research results. Repeatedly inspected sessions are development data.',
        'Drawdown below uses closed-position equity; it does not measure intratrade account drawdown.', '',
        '| Experiment | Symbol | Status | Positions | Net P&L | Fees | Closed drawdown | Full acquisitions | Seconds |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        s = row.get('summary', {})
        metric = lambda key: f'{s[key]:.2f}' if key in s else '—'
        seconds = f"{row['seconds']:.1f}" if 'seconds' in row else '—'
        lines.append(f"| {row['experiment']} | {row['symbol']} | {row['status']} | "
            f"{s.get('position_count','—')} | {metric('net_pnl')} | {metric('fees')} | "
            f"{metric('closed_equity_drawdown')} | {s.get('full_acquisitions','—')} | {seconds} |")
    lines += ['', '## Every completed position', '',
        '| Experiment | Symbol | # | Open UTC | Entry | Exit | Net P&L | Seconds | Exit reason |',
        '| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |']
    for p in positions:
        lines.append(f"| {p['experiment']} | {p['symbol']} | {p['number']} | {p['opened_at']} | "
            f"{float(p['entry_price']):.4f} | {float(p['exit_price'] or 0):.4f} | {float(p['net_pnl']):.2f} | "
            f"{p['duration_seconds']:.1f} | {p['exit_reason']} |")
    for name, content in [('research-ledger.json',json.dumps(report,indent=2)),
                          ('research-ledger.md','\n'.join(lines)+'\n')]:
        target = root/name
        temporary = target.with_suffix('.tmp')
        temporary.write_text(content,encoding='utf-8')
        temporary.replace(target)
    print(f"Experiments: {counts} | audited positions {len(positions)} | report {root/'research-ledger.md'}")


if __name__ == '__main__':
    main()
