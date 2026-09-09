"""Audit every canonical position against pinned completed-bar and journal evidence.

Bar excursions are conservative: only candles wholly inside the holding period
are included. They are offline diagnostics, never strategy inputs or fill claims.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
import sqlite3
from decimal import Decimal
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def entry_fill_location(position, executions, threshold):
    """Describe actual acquisition prices without redefining signal validity."""
    linked = [executions[key] for key in position['execution_ids']]
    buys = sorted((row for row in linked if row['side'] == 'BUY'),
                  key=lambda row: timestamp(row['source_event_time']))
    if not buys:
        raise ValueError('Long position has no linked acquisition executions')
    boundary = Decimal(str(threshold)) if threshold is not None else None
    quantity = sum((Decimal(row['quantity']) for row in buys), Decimal(0))
    below = sum((Decimal(row['quantity']) for row in buys
                 if boundary is not None and Decimal(row['price']) <= boundary), Decimal(0))
    first = buys[0]
    return dict(first_execution_id=first['execution_id'], first_fill_at=first['source_event_time'],
                first_fill_price=first['price'], frozen_signal_threshold=threshold,
                first_fill_above_threshold=(Decimal(first['price']) > boundary if boundary is not None else None),
                acquired_quantity=str(quantity), quantity_at_or_below_threshold=(str(below) if boundary is not None else None),
                interpretation='Fill-location diagnostic; the entry threshold governs the signal, not the price of a later persistent acquisition fill.')


def check_completed_range(gate, frames, session_start, requested):
    """Recompute the recorded entry range from independent prepared candles."""
    context = gate.get('entry_range_context')
    if not context:
        return dict(status='not_recorded')
    at = (gate.get('entry_confirmation') or {}).get('at')
    if at is None or context.get('as_of') != at or at > requested:
        return dict(status='failed', reason='range_confirmation_time_mismatch')
    start = max(session_start, at-context['seconds'])
    prior = [bar for bar, _ in frames if start <= timestamp(bar['bar_end']) < at]
    high = max((bar['high'] for bar in prior), default=None)
    matches = (context.get('ready') is True and context.get('coverage_start') == session_start
        and context.get('window_start') == start and context.get('high') == high
        and context.get('samples') == len(prior) and gate.get('entry_range_high') == high
        and gate.get('entry_range_samples') == len(prior))
    return dict(status='passed' if matches else 'failed', expected_high=high,
        expected_samples=len(prior), expected_window_start=start, confirming_candle_excluded=True)


def audit(results, runtime_root):
    from src.backend.replay_run_service import (
        _prepared_frame_cache_path, _STRATEGY_INDICATOR_FIELDS,
        _definition_from_manifest, _strategy_evaluation_end)
    from src.trading_runtime.journal_evidence import decode_evidence
    run = results['run']
    positions = sorted(results['position_lifecycles'], key=lambda p: p['opened_at'])
    symbols = {p['instrument']['symbol'] for p in positions} or set(run.get('tickers') or [])
    if len(symbols) != 1:
        raise ValueError('Provide a single-symbol run')
    symbol = next(iter(symbols))
    source = dict(run['data_authority']['sources'].get('prepared_strategy_frame_source') or {})
    source['token'] = source.get('revision_token', '')
    ny = ZoneInfo('America/New_York')
    run_dir = runtime_root / run['run_id']
    definition = _definition_from_manifest(json.loads((run_dir/'manifest.json').read_text()), run_dir=run_dir)
    evaluation_end = _strategy_evaluation_end(definition.configuration_revision['payload'],
        session_start=definition.session_start, session_end=definition.session_end)
    cache = _prepared_frame_cache_path(runtime_root,
        start=datetime.fromisoformat(run['session_start']).astimezone(ny),
        end=evaluation_end,
        requests=[(symbol, '1s')], indicator_columns=tuple(sorted(_STRATEGY_INDICATOR_FIELDS)),
        source_revision=source)
    frames = []
    if cache.exists() or positions:
        with readonly(cache) as connection:
            frames = [(json.loads(b), json.loads(i)) for b, i in connection.execute(
                'select bar_json, indicator_json from strategy_frames where ticker=? order by as_of_us', (symbol,))]
    journal = runtime_root / run['run_id'] / 'journal.sqlite3'
    with readonly(journal) as connection:
        evidence = dict(connection.execute('select sha256,payload_json from journal_evidence'))
        decisions = [decode_evidence(json.loads(raw), evidence.get) for raw, in connection.execute(
            "select payload_json from journal where category='strategy_decision' and "
            "json_extract(payload_json,'$.action') in ('enter_long','exit') order by sequence")]
        funding_defers = connection.execute(
            "select count(*) from journal where entity_type='entry_reprice_deferred'").fetchone()[0]
        wait_reasons = dict(connection.execute("select json_extract(payload_json,'$.reason'), count(*) "
            "from journal where category='strategy_decision' and json_extract(payload_json,'$.action')='wait' group by 1"))
        approvals = {}
        for raw, in connection.execute("select payload_json from journal where entity_type='portfolio_decision' "
                                      "and json_extract(payload_json,'$.action')='enter_long' order by sequence"):
            approval = json.loads(raw)
            if float(approval.get('approved_quantity') or 0) > 0:
                approvals[approval['request_id']] = approval
    entries = [d for d in decisions if d['action'] == 'enter_long']
    exits = [d for d in decisions if d['action'] == 'exit']
    executions = {row['execution_id']: row for row in results['executions']}
    if len(executions) != len(results['executions']):
        raise ValueError('Duplicate execution identity in audit input')
    rows = []
    for number, position in enumerate(positions, 1):
        start = timestamp(position['opened_at'])
        end = timestamp(position['closed_at'] or run['session_end'])
        requested = timestamp(position.get('requested_at') or position['opened_at'])
        entry = min(entries, key=lambda d: abs(timestamp(d['event_time']) - requested))
        if abs(timestamp(entry['event_time']) - requested) > .01:
            raise ValueError(f'Position {number} cannot be linked to its entry decision')
        matched_exits = [d for d in exits if requested <= timestamp(d['event_time']) <= end]
        inside = [b for b, _ in frames if timestamp(b['bar_start']) >= start and timestamp(b['bar_end']) <= end]
        prior = [(b, i) for b, i in frames if timestamp(b['bar_end']) <= requested]
        future = [b for b, _ in frames if end <= timestamp(b['bar_start']) < end + 60]
        price = float(position['entry_price'])
        metadata = entry['metadata']
        gate = metadata.get('v5_gate_evidence', {})
        selection = (metadata.get('v5_breakout_selection') or metadata.get('v5_entry_selection')
                     or metadata.get('profit_target_selection') or {})
        row = {k: position.get(k) for k in ('lifecycle_id','opened_at','closed_at','quantity',
            'entry_price','exit_price','gross_pnl','fees','net_pnl','exit_reason')}
        row.update(number=number, duration_seconds=end-start, entry_evidence=metadata,
            exit_evidence=[d['metadata'] for d in matched_exits],
            complete_holding_candles=len(inside),
            complete_bar_mfe_bps=(max(b['high'] for b in inside)/price-1)*10000 if inside else None,
            complete_bar_mae_bps=(min(b['low'] for b in inside)/price-1)*10000 if inside else None,
            next_minute_high=max((b['high'] for b in future), default=None),
            prior_bar=prior[-1] if prior else None,
            entry_gate_passed=metadata.get('reference_price',0) > gate.get('entry_high_threshold',float('inf')),
            initial_selection=selection)
        flags = []
        row['entry_fill_location'] = entry_fill_location(position, executions, gate.get('entry_high_threshold'))
        row['completed_range_audit'] = check_completed_range(gate, frames, timestamp(run['session_start']), requested)
        if row['completed_range_audit']['status'] == 'failed':
            flags.append('canonical_completed_range_mismatch')
        if float(row['net_pnl'] or 0) < 0 and end-start < 2:
            flags.append('loss_within_two_seconds')
        if float(row['gross_pnl'] or 0) >= 0 and float(row['net_pnl'] or 0) < 0:
            flags.append('costs_turn_gross_gain_into_loss')
        if row['next_minute_high'] and row['next_minute_high'] > price*1.05:
            flags.append('hindsight_continuation_after_exit_over_five_percent')
        if not row['entry_gate_passed']:
            flags.append('entry_threshold_violation')
        row['diagnostic_flags'] = flags
        approval = approvals.get(entry['signal_id'])
        row['portfolio_approval'] = approval
        if approval:
            row['acquired_fraction_of_approved'] = float(position['quantity'])/float(approval['approved_quantity'])
            row['approved_quantity_exceeded'] = float(position['quantity']) > float(approval['approved_quantity'])+1e-8
        else:
            row['acquired_fraction_of_approved'] = None
            row['approved_quantity_exceeded'] = None
        rows.append(row)
    equity = peak = 0.0  # Dollar drawdown is invariant to the initial cash offset.
    drawdown = 0
    for row in rows:
        equity += float(row['net_pnl'] or 0)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    summary = dict(position_count=len(rows), net_pnl=sum(float(r['net_pnl'] or 0) for r in rows),
        fees=sum(float(r['fees'] or 0) for r in rows),
        winners=sum(float(r['net_pnl'] or 0)>0 for r in rows),
        under_two_seconds=sum(r['duration_seconds']<2 for r in rows),
        closed_equity_drawdown=drawdown, funding_defers=funding_defers,
        entry_gate_failures=sum(not r['entry_gate_passed'] for r in rows),
        first_fills_at_or_below_signal_threshold=sum(r['entry_fill_location']['first_fill_above_threshold'] is False for r in rows),
        completed_range_checks=sum(r['completed_range_audit']['status'] != 'not_recorded' for r in rows),
        completed_range_failures=sum(r['completed_range_audit']['status'] == 'failed' for r in rows),
        approval_links_missing=sum(r['portfolio_approval'] is None for r in rows),
        approved_quantity_exceeded=sum(r['approved_quantity_exceeded'] is True for r in rows),
        full_acquisitions=sum(r['acquired_fraction_of_approved'] is not None
                              and abs(r['acquired_fraction_of_approved']-1)<1e-8 for r in rows),
        recorded_wait_reasons=wait_reasons,
        exit_reasons=dict(Counter(r['exit_reason'] for r in rows)))
    return dict(schema_version=1, run_id=run['run_id'], symbol=symbol,
        frame_cache=str(cache), frame_evaluation_end=evaluation_end.isoformat(),
        data_authority=run['data_authority'], summary=summary, positions=rows,
        limitations=['Excursions omit partial entry and exit candles.',
                      'Closed-equity drawdown excludes unrealized intratrade drawdown.',
                      'Next-minute high is hindsight only; it is not an executable exit.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    args = parser.parse_args()
    result = audit(json.loads(args.results.read_text(encoding='utf-8')), args.runtime_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    s = result['summary']
    print(f"Audit complete | {result['symbol']} | positions {s['position_count']} | "
          f"net ${s['net_pnl']:.2f} | fees ${s['fees']:.2f} | entry violations {s['entry_gate_failures']}")
    print(f"Evidence: {args.output}")


if __name__ == '__main__':
    main()
