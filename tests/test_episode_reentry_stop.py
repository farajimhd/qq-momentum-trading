import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from math import floor
from types import SimpleNamespace

from src.trading_runtime import strategy_engine as S, v5_macd_episode as M
from tests.test_v5_macd_episode import rejection_setup
from tests.test_long_momentum_strategy import assignment


def setup():
    p, state, o = rejection_setup()
    p['episode_management'] = dict(same_episode_reentry_stop=True, reentry_stop_offset_bps=5.)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    state['v5_breakout_state']['prior_max'] = 103.
    return p, state, o


def test_only_same_acquired_episode_uses_buffered_prior_body_high():
    p, state, o = setup()
    original = M.select(o, p, state)
    assert original['stop_selection']['source'] != 'macd_episode_high'
    state['last_acquired_macd_episode'] = original['episode_started_at']
    state = json.loads(json.dumps(state))  # recovery preserves the exact episode witness
    selected = M.select(o, p, state)
    tick = p['execution']['tick_size']
    expected = floor((103.-max(tick, 103.*5/10000))/tick+1e-9)*tick
    assert selected['stop'] == expected < 103.
    assert selected['stop_selection']['high'] == 103.  # not the breakout candle price or its wick
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), o)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.invalidation_price == expected
    assert all(s.stop.price == expected for s in entry.resolved_protection_profile().slices)
    state.update(v5_entry_selection=selected, initial_stop=expected, active_stop=expected)
    assert M.manage(replace(o, position_quantity=10, average_price=104.), p, state) == expected
    state['last_acquired_macd_episode'] -= 1
    assert M.select(o, p, state)['stop_selection']['source'] != 'macd_episode_high'


def test_completed_macd_reset_starts_new_episode_but_missing_sample_does_not():
    p, state, o = setup()
    p['macd_evaluation_mode'] = 'completed_1s'
    def closed(seconds, line):
        return replace(o, observed_at=o.observed_at+timedelta(seconds=seconds),
                       source_timeframe='1s', evaluation_events=('bar_close',), macd_line=line)
    M.observe(closed(1, 1.), p, state)
    first = state['v5_breakout_state']['episode_started_at']
    M.observe(closed(2, None), p, state)
    M.observe(closed(3, 1.), p, state)
    assert state['v5_breakout_state']['episode_started_at'] == first
    M.observe(closed(4, 0.), p, state)
    M.observe(closed(5, 1.), p, state)
    assert state['v5_breakout_state']['episode_started_at'] > first


def test_fill_callback_marks_acquired_episode_only_after_actual_fill():
    async def check():
        p, state, o = setup()
        selected = M.select(o, p, state)
        state['v5_entry_selection'] = selected
        assigned = assignment(strategy_revision=47, parameters=p, state=state)
        strategy = S.AssignedLongMomentumStrategy([assigned])
        snapshot = SimpleNamespace(action='enter_long', assignment_id=assigned.assignment_id,
            state='acknowledged', fill_incremental_quantity=0., updated_at=o.observed_at)
        await strategy.on_order_group_update(snapshot, aggregate_position_quantity=0)
        assert 'last_acquired_macd_episode' not in strategy.assignments()[0].state
        snapshot.state = 'partially_filled'
        snapshot.fill_incremental_quantity = 10.
        await strategy.on_order_group_update(snapshot, aggregate_position_quantity=10)
        assert strategy.assignments()[0].state['last_acquired_macd_episode'] == selected['episode_started_at']
    asyncio.run(check())
