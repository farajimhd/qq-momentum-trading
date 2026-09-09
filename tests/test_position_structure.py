from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import json

import pytest

from src.trading_runtime.position_structure import observe
from src.trading_runtime.v5_episode_management import DEFAULTS
from src.trading_runtime import strategy_engine as S, v5_macd_episode as M
from tests.test_v5_macd_episode import rejection_setup


def harness():
    policy = dict(DEFAULTS, position_structure_enabled=True, swing_left_bars=1,
                  swing_right_bars=1, swing_buffer_atr_multiple=0., swing_buffer_bps=0., rejection_closes=2)
    d = {}
    def feed(at, price, high=None, low=None, closed=True, held=100):
        o = SimpleNamespace(observed_at=datetime.fromtimestamp(at, timezone.utc), price=price,
            bar_open=price, bar_high=high or price, bar_low=low or price, position_quantity=held)
        observe(o, d, closed, policy, [])
        return d.get('position_structure', {})
    feed(100, 10., closed=False)
    return d, feed


def test_confirmed_swings_hold_pullback_then_exit_only_below_support():
    d, feed = harness()
    feed(101, 10.)
    feed(102, 9., low=8.9)
    assert not d['position_structure'].get('low')
    feed(103, 10.)
    assert d['position_structure']['low']['confirmed_at'] == 103
    feed(104, 11., high=11.1)
    feed(105, 10.)
    resistance = d['position_structure']['resistance']
    assert resistance['support']['low'] == 8.9
    feed(106, 9.2)
    assert not d.get('position_structure_failure')
    feed(107, 8.8)
    feed(107.5, 8., closed=False)
    feed(107, 8.8)  # duplicate close
    assert not d.get('position_structure_failure')
    feed(108, 8.7)
    assert d['position_structure_failure']['reason'] == 'resistance_failure'
    assert d['position_structure_failure']['support']['confirmed_at'] == 103


def test_no_resistance_no_discretionary_exit_and_flat_resets():
    d, feed = harness()
    for at, price in enumerate([10., 9., 8., 7.], 101):
        feed(at, price)
    assert not d.get('position_structure_failure')
    feed(105, 7., held=0)
    assert not d.get('position_structure')


def test_break_earns_support_and_restart_preserves_it_without_macd_dependency():
    d, feed = harness()
    for at, price in enumerate([10., 9., 10., 11., 10., 11.], 101):
        feed(at, price)
    assert not d['position_structure'].get('established_support')  # equality is not a break
    feed(107, 11.2)
    assert d['position_structure']['established_support']['low'] == 10.
    d.update(json.loads(json.dumps(d)))
    feed(108, 9.8)
    feed(109, 9.7)
    assert d['position_structure_failure']['reason'] == 'established_support_failure'


def test_gap_cannot_confirm_pivot_and_memory_is_bounded():
    d, feed = harness()
    feed(101, 10.)
    feed(102, 9.)
    feed(104, 10.)
    assert not d['position_structure'].get('low')
    for at in range(105, 200):
        feed(at, 10.+at/100)
    assert len(d['position_structure']['bars']) == 3


@pytest.mark.parametrize('patch', [dict(take_profit_fraction=0.), dict(profit_trail_atr_multiple=2.),
    dict(swing_right_bars=0), dict(swing_left_bars=1.5)])
def test_structure_configuration_rejects_unprotected_runner_or_atr_exit(patch):
    p, _, _ = rejection_setup()
    p['episode_management'] = dict(position_structure_enabled=True, **patch)
    with pytest.raises(ValueError):
        S.resolve_long_momentum_parameters(p, revision=47)


def test_engine_entry_contains_full_target_and_macd_reset_preserves_position():
    from tests.test_long_momentum_strategy import assignment
    p, state, o = rejection_setup()
    p['episode_management'] = dict(position_structure_enabled=True, swing_buffer_atr_multiple=0.)
    p['protection_profile_catalog'] = {'test': {'profile_id': 'test', 'slices': [
        {'slice_id': 'target', 'quantity_fraction': 1., 'stop': {'rule_type': 'fixed_price'}}]}}
    p['phase_policy']['initial_entry'] = dict(order_intent=dict(protection_profile='test',
        execution_policy='adaptive_urgent', partial_fill_policy='complete_remainder'))
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), o)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    slices = entry.resolved_protection_profile().slices
    assert len(slices) == 1 and slices[0].quantity_fraction == 1.
    assert slices[0].profit_target_price > o.price > slices[0].stop.price > 0
    p = S.resolve_long_momentum_parameters(p, revision=47)
    held = replace(o, position_quantity=100., average_price=o.price,
                   observed_at=o.observed_at+timedelta(seconds=1))
    M.observe(held, p, state)
    acquired = state['v5_breakout_state']['position_structure']['acquired_observed_at']
    M.observe(replace(held, observed_at=held.observed_at+timedelta(seconds=1),
                      macd_line=0., macd_signal=1.), p, state)
    assert not state['v5_breakout_state']['macd_open']
    assert state['v5_breakout_state']['position_structure']['acquired_observed_at'] == acquired
