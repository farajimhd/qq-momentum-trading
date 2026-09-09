"""Run an immutable candidate on baseline replay definitions, with a restart ledger."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, time as clock_time
import hashlib
import json
import subprocess
import time


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def experiment_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'experiment.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


async def run(args):
    from src.backend.replay_run_service import ReplayRunController, _definition_from_manifest
    from src.backend.trading_configuration_service import candidate_runtime_configuration_snapshot
    from src.backend.canonical_trading_service import trading_state_payload
    from scripts.audit_strategy_positions import audit
    args.output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    source_paths = sorted((repo/'src/trading_runtime').glob('*.py')) + [
        repo/'src/backend/replay_run_service.py', repo/'src/backend/swing_book_cursor.py']
    source_hashes = {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in source_paths}
    source_identity = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    identity = dict(candidate=args.candidate, plan=args.plan, baseline_runs=args.baseline_run,
                    simulation_profile=args.simulation_profile, source_identity=source_identity,
                    session_date=args.session_date, start_time=args.start_time, end_time=args.end_time)
    ledger_path = args.output/'ledger.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(identity=identity, cases={})
    if ledger['identity'] != identity:
        raise ValueError('Experiment identity differs from existing ledger; use a new output directory')
    save(args.output/'source-provenance.json', dict(source_identity=source_identity, files=source_hashes,
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()))
    config = await asyncio.to_thread(candidate_runtime_configuration_snapshot, 'backtest',
        candidate_id=args.candidate, run_plan_id=args.plan)
    save(ledger_path, ledger)
    for index, baseline in enumerate(args.baseline_run):
        if ledger['cases'].get(baseline, {}).get('status') == 'completed':
            print(f'Skipped completed case {baseline}', flush=True)
            continue
        old = args.runtime_root/baseline
        definition = _definition_from_manifest(json.loads((old/'manifest.json').read_text()), run_dir=old)
        definition = replace(definition, configuration_revision=config,
            simulation_profile=args.simulation_profile or definition.simulation_profile)
        if args.session_date:
            session = date.fromisoformat(args.session_date)
            definition = replace(definition, session_date=session, final_session_date=session)
        if args.start_time:
            definition = replace(definition, start_time=clock_time.fromisoformat(args.start_time))
        if args.end_time:
            definition = replace(definition, end_time=clock_time.fromisoformat(args.end_time))
        controller = ReplayRunController(definition, runtime_root=args.runtime_root)
        started = time.monotonic()
        case = dict(status='active', run_id=controller.run_id, baseline=baseline,
                    symbol=definition.tickers[0])
        if baseline in ledger['cases']:
            ledger.setdefault('prior_attempts', []).append(dict(ledger['cases'][baseline]))
        ledger['cases'][baseline] = case
        save(ledger_path, ledger)
        print(f"Active {case['symbol']} | queued {len(args.baseline_run)-index-1} | run {controller.run_id}", flush=True)
        try:
            await controller.start()
            while not controller._task.done():
                await asyncio.wait({controller._task}, timeout=20)
                if not controller._task.done():
                    print(f"Active {case['symbol']} | {controller.status} | events {controller.processed_events:,} | "
                          f"elapsed {time.monotonic()-started:.0f}s", flush=True)
            await controller._task
            if controller.status != 'completed':
                raise RuntimeError(f'{controller.status}: {controller.error}')
            trading = await asyncio.to_thread(trading_state_payload, controller._runtime.projected_snapshot(),
                include_strategy_activity=False, protection_as_of=controller.current_time)
            results = dict(trading, run=controller.snapshot())
            save(args.output/f"{case['symbol']}-results.json", results)
            report = await asyncio.to_thread(audit, results, args.runtime_root)
            save(args.output/f"{case['symbol']}-audit.json", report)
            case.update(status='completed', seconds=time.monotonic()-started,
                        processed_events=controller.processed_events, summary=report['summary'])
            print(f"Completed {case['symbol']} | positions {report['summary']['position_count']} | "
                  f"net ${report['summary']['net_pnl']:.2f} | elapsed {case['seconds']:.1f}s", flush=True)
        except BaseException as exc:
            if controller._task and not controller._task.done():
                controller._task.cancel()
                await asyncio.gather(controller._task, return_exceptions=True)
            case.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 'failed',
                        error=str(exc), seconds=time.monotonic()-started)
            raise
        finally:
            save(ledger_path, ledger)
    print(f"Experiment complete | cases {len(ledger['cases'])} | ledger {ledger_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--baseline-run', action='append', required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--simulation-profile', choices=['baseline', 'stress'])
    parser.add_argument('--session-date', help='ISO session date; otherwise preserve each baseline date')
    parser.add_argument('--start-time', help='New York start time; otherwise preserve the baseline window')
    parser.add_argument('--end-time', help='New York end time; otherwise preserve the baseline window')
    args = parser.parse_args()
    with experiment_lock(args.output):
        asyncio.run(run(args))


if __name__ == '__main__':
    main()
