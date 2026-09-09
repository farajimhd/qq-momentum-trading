from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import pytest

from src.trading_runtime.completed_candle_range import CompletedCandleRange
from src.trading_runtime import v5_macd_episode as M, strategy_engine as S
from tests.test_v5_macd_episode import rejection_setup


def test_range_excludes_confirming_candle_and_expires_only_past_window():
    window = CompletedCandleRange(10, 100)
    for at, high in [(100, 15), (105, 12), (110, 20)]:
        window.observe(at, high)
    assert window.context(110)['high'] == 15
    assert window.context(110)['samples'] == 2
    window.observe(111, 13)
    assert window.context(111)['high'] == 20
    assert window.context(111)['samples'] == 2
    assert not window.context(112)['ready']
    assert CompletedCandleRange.restore(window.checkpoint()).context(111) == window.context(111)
    window.observe(111, 13)  # Repeated remembering of the same final frame is idempotent.
    with pytest.raises(ValueError, match='conflicting'):
        window.observe(111, 14)
    corrupt = window.checkpoint()
    corrupt['rows'].append(corrupt['rows'][-1])
    with pytest.raises(ValueError, match='duplicate'):
        CompletedCandleRange.restore(corrupt)


def test_pre_admission_range_blocks_a_break_above_only_post_admission_candles():
    p, state, original = rejection_setup()
    p['episode_management'] = dict(entry_on_close=True, entry_range_seconds=600., require_range_context=True)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    now = original.observed_at.timestamp()+1
    window = CompletedCandleRange(600, now-600)
    window.observe(now-300, 110.)  # A valid high before the strategy became eligible.
    window.observe(now-1, 103.)
    window.observe(now, 104.)
    closed = replace(original, observed_at=original.observed_at+timedelta(seconds=1),
                     price=104., bar_high=104., source_timeframe='1s', evaluation_events=('bar_close',),
                     completed_range_context=window.context(now))
    M.observe(closed, p, state)
    assert state['v5_breakout_state']['entry_range_high'] == 110.
    assert M.select(closed, p, state)['reason'] == 'v5_period_high_not_reclaimed'
    missing_state = {}
    missing = replace(closed, completed_range_context={})
    M.observe(missing, p, missing_state)
    assert M.select(missing, p, missing_state)['reason'] == 'v5_canonical_range_history_unavailable'
    future = dict(window.context(now), as_of=now+1)
    future_state = {}
    future_observation = replace(closed, completed_range_context=future)
    M.observe(future_observation, p, future_state)
    assert M.select(future_observation, p, future_state)['reason'] == 'v5_canonical_range_history_unavailable'
    assert not state['v5_breakout_state'].get('entry_range_history')


def test_replay_remembers_pre_activation_frames_without_evaluating_strategy():
    from src.backend.replay_run_service import ReplayRunController
    controller = object.__new__(ReplayRunController)
    p, _, original = rejection_setup()
    start = original.observed_at-timedelta(minutes=10)
    controller.definition = SimpleNamespace(experimental_structure_book='', session_start=start,
        configuration_revision={'payload': {'strategy': {'parameters': {
            'episode_management': {'entry_range_seconds': 600.}}}}})
    early = SimpleNamespace(ticker='TEST', timeframe='1s', as_of=start+timedelta(seconds=1), bar={'high':110.})
    current = SimpleNamespace(ticker='TEST', timeframe='1s', as_of=original.observed_at, bar={'high':104.})
    controller._remember_strategy_frame(early)
    controller._remember_strategy_frame(current)
    controller._remember_strategy_frame(current)
    context = controller._completed_range_context(current)
    assert context['high'] == 110.
    assert context['samples'] == 1
