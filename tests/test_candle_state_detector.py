import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from src.trading_runtime.continuation_detector import observe
from src.trading_runtime.v5_episode_management import DEFAULTS


def feed(d, at, opened, close, held=0, closed=True, low=None):
    o = SimpleNamespace(observed_at=datetime.fromtimestamp(at, timezone.utc),
        price=close, bar_open=opened, bar_high=max(opened, close),
        bar_low=min(opened, close) if low is None else low,
        position_quantity=held, source_timeframe='1s' if closed else '',
        evaluation_events=('bar_close',) if closed else ('market_data_update',))
    observe(o, d, dict(DEFAULTS, detector_candle_states_enabled=True))
    return d['continuation_detector']['decision']


def test_every_close_is_fresh_and_independent_of_position_lifecycle():
    candles = [(10, 11), (11, 10.5), (10.5, 10.9), (10.9, 11.2), (11.2, 11.2)]
    histories = []
    for quantities in ([0]*5, [10]*5, [10, 0, 0, 10, 0]):
        d = dict(macd_open=True, episode_started_at=100)
        histories.append([copy.deepcopy(feed(d, 100+i, *bar, quantities[i])) for i, bar in enumerate(candles)])
    assert histories[0] == histories[1] == histories[2]
    assert [r['state'] for r in histories[0]] == ['advance', 'pullback', 'recovery', 'continuation', 'advance']
    assert [r['sequence'] for r in histories[0]] == [1, 2, 3, 4, 5]
    assert histories[0][1]['prior_advance_high'] == 11
    assert histories[0][1]['candle_start'] == datetime.fromtimestamp(100, timezone.utc).isoformat()


def test_episode_change_at_close_while_held_then_stop_out_preserves_high():
    d = dict(macd_open=True, episode_started_at=100)
    feed(d, 100, 10, 11, held=10)
    d['episode_started_at'] = 101
    feed(d, 101, 11, 12, held=10)
    before = json.dumps(d, sort_keys=True)
    feed(d, 101.1, 12, 11.4, held=0, closed=False)
    assert json.dumps(d, sort_keys=True) == before
    result = feed(d, 102, 12, 11.5, held=0)
    assert result['episode'] == 101 and result['prior_advance_high'] == 12
    assert result['state'] == 'pullback'


def test_only_closed_candles_confirm_rejection_and_no_duplicate_rewrite():
    d = dict(macd_open=True, episode_started_at=100)
    feed(d, 100, 10, 11)
    feed(d, 101, 11, 10.5, low=10.4)
    assert feed(d, 102, 10.5, 10.8)['state'] == 'recovery'
    before = json.dumps(d, sort_keys=True)
    feed(d, 102.5, 10.8, 10.3, closed=False)
    feed(d, 102, 10.8, 10.3)
    feed(d, 101, 10.8, 10.3)
    assert json.dumps(d, sort_keys=True) == before
    assert feed(d, 103, 10.8, 10.3)['state'] == 'rejection'
    resumed = json.loads(json.dumps(d))
    assert feed(resumed, 104, 10.3, 11.2) == feed(d, 104, 10.3, 11.2)
    assert d['continuation_detector']['state'] == 'continuation'


def test_gap_invalid_and_inactive_are_explicit():
    d = dict(macd_open=True, episode_started_at=100)
    feed(d, 100, 10, 11)
    assert feed(d, 102, 11, 10)['reason'] == 'candle_gap'
    assert feed(d, 103, 10, float('nan'))['reason'] == 'invalid_candle'
    d.update(macd_open=False, episode_started_at=None)
    assert feed(d, 104, 10, 10)['state'] == 'inactive'


def test_engine_does_not_exit_new_position_on_preentry_rejection():
    from dataclasses import replace
    from datetime import timedelta
    from tests.test_defensive_structure import setup
    from tests.test_long_momentum_strategy import assignment
    from src.trading_runtime import strategy_engine as S
    p, state, o, _ = setup()
    p['macd_evaluation_mode'] = 'completed_1s'
    p['episode_management'].update(continuation_detector_enabled=True, detector_candle_states_enabled=True,
        completed_body_reentry_enabled=True, same_episode_reentry_stop=True)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    closed_at = o.observed_at
    state.update(initial_stop=99., active_stop=99., entry_reference_price=100.)
    state['v5_breakout_state'].update(fill_stop_initialized=True, continuation_detector={
        'state':'rejection', 'decision':{'state':'rejection', 'candle_end':closed_at.isoformat(),
        'reason':'failed_recovery_support_break'}})
    o = replace(o, observed_at=closed_at+timedelta(seconds=1), source_timeframe='',
                evaluation_events=('market_data_update',), average_price=100.)
    engine = S.LongMomentumStrategyEngine(revision=47)
    for offset, expected in [(0, 0), (.5, 0), (-1, 1)]:
        current = copy.deepcopy(state)
        current['entry_at'] = (closed_at+timedelta(seconds=offset)).isoformat()
        result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=current), o)
        assert len([i for i in result.evaluation.intents if i.action == 'exit']) == expected


def test_passive_replay_records_candles_before_any_assignment(tmp_path):
    import asyncio
    from src.backend.replay_run_service import ReplayRunController, ReplayDerivedFrame
    from src.trading_runtime.journal import TradingJournal
    from tests.test_defensive_structure import setup
    p, _, _, _ = setup()
    p['macd_evaluation_mode'] = 'completed_1s'
    p['episode_management'].update(continuation_detector_enabled=True, detector_candle_states_enabled=True,
        completed_body_reentry_enabled=True, same_episode_reentry_stop=True)
    c = ReplayRunController.__new__(ReplayRunController)
    c.definition = SimpleNamespace(configuration_revision={'payload':{'strategy':{
        'parameters':p, 'revision':47, 'strategy_id':'test'}}}, experimental_structure_book=None)
    c.run_id = 'passive'
    c._candle_detector_states = {}
    c._journal = TradingJournal(tmp_path/'passive.sqlite3')
    try:
        for at, opened, close in [(100,10,11),(101,11,10.5),(102,10.5,10.9)]:
            frame=ReplayDerivedFrame(as_of=datetime.fromtimestamp(at,timezone.utc),
                bar={'open':opened,'high':max(opened,close),'low':min(opened,close),'close':close},
                indicator={'macd_line':.1,'macd_signal':0},sequence=at,ticker='T',timeframe='1s')
            asyncio.run(c._observe_episode_candle(frame))
            asyncio.run(c._observe_episode_candle(frame))
        rows=c._journal.strategy_activity_records(run_id='passive',compact=True)
        assert len(rows)==3
        assert [r.payload['metadata']['continuation_detector']['state'] for r in reversed(rows)] == ['advance','pullback','recovery']
        assert all(r.payload['action']=='observe' for r in rows)
        assert c._candle_detector_states['T']['continuation_detector']['high']==11
    finally:
        c._journal.close()
