from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

from src.trading_runtime.continuation_detector import observe
from src.trading_runtime.v5_episode_management import DEFAULTS


def harness():
    d = dict(macd_open=True, episode_started_at=100., entry_high_threshold=11.02)
    policy = dict(DEFAULTS, adaptive_target_body_half_life=3.)
    def feed(at, price, *, low=None, opened=None, closed=True, held=10):
        opened = price if opened is None else opened
        o = SimpleNamespace(observed_at=datetime.fromtimestamp(at, timezone.utc), price=price,
            bar_open=opened, bar_high=max(opened, price), bar_low=min(opened, price) if low is None else low,
            position_quantity=held, source_timeframe='1s' if closed else '',
            evaluation_events=('bar_close',) if closed else ('market_data_update',))
        observe(o, d, policy)
        return d['continuation_detector']
    return d, feed


def test_pullback_and_candidate_hold_then_recovery_break_exits_intrabar():
    d, feed = harness()
    feed(100, 10)
    feed(101, 11, opened=10)
    d['position_structure'] = {'resistance': {'lower':10.9, 'upper':11.1, 'source':'internal_swing', 'confirmed_at':102}}
    s = feed(102, 10.5, low=10.4, opened=11)
    assert s['state'] == 'pullback' and s['decision']['action'] == 'hold'
    s = feed(103, 10.8, opened=10.5)
    assert s['state'] == 'recovery' and s['pullback']['floor'] == 10.4
    assert feed(103.2, 10.4, closed=False)['state'] == 'recovery'  # equality is no break
    s = feed(103.3, 10.39, closed=False)
    assert s['state'] == 'rejection' and s['decision']['action'] == 'exit'
    assert s['decision']['effective_at'] == datetime.fromtimestamp(103.3, timezone.utc).isoformat()
    assert s['decision']['recovery_floor'] == 10.4


def test_continuation_no_exit_and_bounded_state_serialization():
    d, feed = harness()
    for at, p in [(100,10),(101,11),(102,10.5),(103,10.8),(104,11.1)]:
        feed(at,p)
    assert d['continuation_detector']['state'] == 'advance'
    assert 'pullback' not in d['continuation_detector']
    for at in range(105, 300):
        feed(at, 11.2+(at-105)*.01)
    assert len(d['continuation_detector']['bodies']) <= DEFAULTS['adaptive_target_body_window']
    snapshot = json.loads(json.dumps(d))
    feed(299, 1)  # duplicate close cannot rewrite structure
    assert d['continuation_detector']['closed_at'] == snapshot['continuation_detector']['closed_at']


def test_flat_reentry_uses_prior_body_threshold_and_rejection_rearms():
    d, feed = harness()
    feed(100,10)
    d['continuation_detector']['rejected'] = 'failed_recovery_support_break'
    assert feed(100.1,11.02,held=0,closed=False)['decision']['action'] == 'wait'
    assert feed(100.2,11.03,held=0,closed=False)['state'] == 'continuation'
    assert feed(100.3,10.9,held=0,closed=False)['state'] == 'pullback'
    assert feed(100.4,11.04,held=0,closed=False)['state'] == 'continuation'


def test_gap_cannot_confirm_recovery_and_episode_resets_flat_only():
    d, feed = harness()
    feed(100,10); feed(101,11); feed(102,10.5)
    assert feed(104,10.8)['state'] == 'unresolved'
    d.update(macd_open=False, episode_started_at=None)
    assert feed(104.1,10.7,closed=False)['state'] != 'inactive'
    assert feed(104.2,10.7,held=0,closed=False)['state'] == 'inactive'


def test_confirmed_resistance_support_break_acts_on_first_tick():
    d, feed = harness()
    feed(100,10)
    d['position_structure'] = {'resistance': {'lower':11., 'upper':11.1,
        'support': {'boundary':9.8}, 'source':'v5', 'confirmed_at':101}}
    assert feed(101,10.1)['decision']['action'] == 'hold'
    assert feed(101.1,9.8,closed=False)['decision']['action'] == 'hold'
    assert feed(101.2,9.79,closed=False)['decision']['reason'] == 'resistance_support_break'


def test_runtime_logs_detector_transitions_without_tick_duplicates(tmp_path):
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.journal import TradingJournal
    from src.trading_runtime.signals import StrategySignal, StrategyEvaluation
    journal = TradingJournal(tmp_path/'signals.sqlite3')
    runtime = TradingRuntime.__new__(TradingRuntime)
    runtime.run_id = 'r'
    runtime.config = SimpleNamespace(strategy_id='s',strategy_revision=47)
    runtime.journal = journal
    runtime._last_wait_decision_signatures = {}
    try:
        for i, sequence in enumerate((1,1,2,2,3)):
            signal = StrategySignal(signal_id=str(i),signal_type='waiting',ticker='T',
                event_time=datetime.fromtimestamp(100+i,timezone.utc),action='wait',direction='neutral',
                score=0.,confidence=0.,reason='waiting',metadata={'continuation_detector':{'sequence':sequence}})
            runtime._record_strategy_signals(StrategyEvaluation(signals=(signal,)), 'a')
        rows = journal.strategy_activity_records(run_id='r',consequential_only=True,compact=True)
        assert len(rows) == 3
    finally:
        journal.close()


def test_strategy_candidate_contact_holds_and_detector_rejection_exits():
    from tests.test_defensive_structure import setup
    from tests.test_long_momentum_strategy import assignment
    from src.trading_runtime import strategy_engine as S
    p, state, o, _ = setup()
    p['macd_evaluation_mode'] = 'completed_1s'
    p['episode_management'].update(continuation_detector_enabled=True, completed_body_reentry_enabled=True,
                                  same_episode_reentry_stop=True)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    state.update(initial_stop=99., active_stop=99., entry_reference_price=100.)
    d = state['v5_breakout_state']
    d.update(position_structure={'resistance': {'lower':103.,'upper':104.,'source':'internal_swing',
        'confirmed_at':o.observed_at.timestamp()}}, fill_stop_initialized=True)
    o = replace(o, observed_at=o.observed_at+timedelta(seconds=1), evaluation_events=('market_data_update',),
                source_timeframe='', average_price=100.)
    engine = S.LongMomentumStrategyEngine(revision=47)
    a = assignment(strategy_revision=47, parameters=p, state=state)
    first = engine.evaluate(a, o)
    assert not [i for i in first.evaluation.intents if i.action == 'exit']
    state = first.state
    # Feed the actual detector failure through the normal engine route.
    state['v5_breakout_state']['continuation_detector']['rejected'] = 'failed_recovery_support_break'
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state),
                             replace(o, observed_at=o.observed_at+timedelta(milliseconds=1)))
    exits = [i for i in result.evaluation.intents if i.action == 'exit']
    assert len(exits) == 1 and exits[0].quantity == o.position_quantity


def test_wait_detector_survives_journal_compact_chart_and_asof(tmp_path):
    from src.trading_runtime.journal import TradingJournal
    from src.backend.trading_runtime_service import strategy_activity_payload
    from src.backend.replay_run_service import _compact_strategy_chart_activity_rows
    at = datetime(2026,1,1,tzinfo=timezone.utc)
    journal = TradingJournal(tmp_path/'journal.sqlite3')
    try:
        for i, state in enumerate(('pullback','continuation')):
            journal.append(run_id='r', category='strategy_decision', entity_type='signal', entity_id=str(i),
                event_time=at+timedelta(seconds=i), payload=dict(action='wait',ticker='T',
                metadata={'continuation_detector':dict(sequence=i+1,state=state, effective_at=(at+timedelta(seconds=i)).isoformat())}))
        rows = strategy_activity_payload(journal=journal,run_id='r',ticker='T',as_of=at,
            consequential_only=True,include_decision_evidence=False)['rows']
        compact = _compact_strategy_chart_activity_rows(rows)
        assert len(compact) == 1
        assert 'detector:pullback' in rows[0]['gates']
        assert compact[0]['chart_plan']['continuation_detector']['state'] == 'pullback'
    finally:
        journal.close()
