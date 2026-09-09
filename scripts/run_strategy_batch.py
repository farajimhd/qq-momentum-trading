"""Execute a frozen sequential research batch with shared preparation caches."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
import hashlib
import json
from types import SimpleNamespace

from scripts.run_strategy_experiment import experiment_lock, run, save


def specifications(value, output_root, runtime_root):
    if not isinstance(value, list) or not value:
        raise ValueError('Batch manifest must contain a nonempty list of experiments')
    optional = dict(simulation_profile=None, session_date=None, start_time=None,
                    end_time=None, new_order_activation_delay_ms=None)
    required = {'name', 'candidate', 'plan', 'baseline_run'}
    seen, result = set(), []
    for row in value:
        if not isinstance(row, dict) or set(row)-required-set(optional) or required-set(row):
            raise ValueError('Experiment fields do not match the explicit batch contract')
        name = row['name']
        if (not isinstance(name, str) or name in ('', '.', '..')
                or any(c in name for c in '/\\:') or name.casefold() in seen):
            raise ValueError('Experiment names must be unique directory names')
        if (not isinstance(row['baseline_run'], list) or not row['baseline_run']
                or any(not isinstance(v, str) or not v for v in row['baseline_run'])):
            raise ValueError('Each experiment requires explicit baseline run identities')
        seen.add(name.casefold())
        fields = {k: v for k, v in row.items() if k != 'name'}
        result.append(SimpleNamespace(**dict(optional, **fields),
            output=output_root/name, runtime_root=runtime_root))
    return result


async def batch(args):
    content = args.manifest.read_bytes()
    cases = specifications(json.loads(content), args.output_root, args.runtime_root)
    ledger_path = args.manifest.with_suffix('.batch-ledger.json')
    identity = dict(manifest_sha256=hashlib.sha256(content).hexdigest(),
                    output_root=str(args.output_root.resolve()), runtime_root=str(args.runtime_root.resolve()))
    previous = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    if previous and previous['identity'] != identity:
        raise ValueError('Batch identity changed; preserve its prior manifest and use a new one')
    ledger = dict(identity=identity, cases={}, prior_attempts=previous.get('prior_attempts', []))
    if previous:
        ledger['prior_attempts'].append(previous['cases'])
    failures = 0
    for index, case in enumerate(cases):
        name = case.output.name
        ledger['cases'][name] = dict(status='active')
        save(ledger_path, ledger)
        print(f'Batch active {name} | queued {len(cases)-index-1} | completed {index-failures} | failed {failures}', flush=True)
        try:
            with experiment_lock(case.output):
                await run(case)
            ledger['cases'][name] = dict(status='completed')
        except (KeyboardInterrupt, asyncio.CancelledError):
            ledger['cases'][name] = dict(status='interrupted')
            raise
        except Exception as exc:
            failures += 1
            ledger['cases'][name] = dict(status='failed', error=str(exc))
            print(f'Batch failed {name} | {exc}', flush=True)
            if not args.keep_going:
                raise
        finally:
            save(ledger_path, ledger)
    print(f'Batch finished | completed {len(cases)-failures} | failed {failures} | {ledger_path}', flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--keep-going', action='store_true', help='Record failed independent cases and continue the frozen batch')
    args = parser.parse_args()
    # Existing per-experiment locks prevent duplicate writers. The batch also
    # holds its own lock so two invocations cannot overwrite the queue ledger.
    with experiment_lock(args.manifest.with_suffix('.batch-lock')):
        raise SystemExit(asyncio.run(batch(args)))


if __name__ == '__main__':
    main()
