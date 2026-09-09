"""Recover reporting for a completed replay without executing that replay again."""
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
from scripts.audit_strategy_positions import audit
from scripts.run_strategy_experiment import experiment_lock, save


def validate_result(case, result):
    run = result.get('run') or {}
    if case.get('status') not in ('failed', 'interrupted'):
        raise ValueError('Recover only a failed or interrupted reporting case')
    if run.get('run_id') != case.get('run_id') or run.get('status') != 'completed':
        raise ValueError('Recovery requires the matching completed replay result')
    if run.get('tickers') != [case.get('symbol')]:
        raise ValueError('Recovered result ticker differs from the recorded case')


def recover(experiment, runtime_root):
    with experiment_lock(experiment):
        ledger_path = experiment/'ledger.json'
        ledger = json.loads(ledger_path.read_text())
        completed = skipped = 0
        for baseline, case in ledger['cases'].items():
            if case.get('status') == 'completed':
                skipped += 1
                continue
            result_path = experiment/f"{case['symbol']}-results.json"
            result = json.loads(result_path.read_text())
            validate_result(case, result)
            manifest = json.loads((runtime_root/case['run_id']/'manifest.json').read_text())
            if (manifest.get('run') or {}).get('status') != 'completed':
                raise ValueError('Persisted replay manifest is not completed')
            report = audit(result, runtime_root)
            output = experiment/f"{case['symbol']}-audit.json"
            if output.exists():
                if json.loads(output.read_text()) != report:
                    raise ValueError('Existing audit differs; preserve it and inspect the reporting inputs')
            else:
                save(output, report)
            ledger.setdefault('report_recoveries', []).append(dict(
                recovered_at=datetime.now(timezone.utc).isoformat(), baseline=baseline,
                previous_case=dict(case), result_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
                audit_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                audit_source_sha256=hashlib.sha256(Path(sys.modules[audit.__module__].__file__).read_bytes()).hexdigest(),
                replay_reexecuted=False))
            case.pop('error', None)
            case.update(status='completed', processed_events=result['run']['processed_events'], summary=report['summary'])
            save(ledger_path, ledger)
            completed += 1
        print(f'Audit recovery complete | recovered {completed} | skipped {skipped} | no replay executed | {experiment}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    args = parser.parse_args()
    recover(args.experiment, args.runtime_root)


if __name__ == '__main__':
    main()
