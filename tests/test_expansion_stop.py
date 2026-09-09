from dataclasses import replace
from datetime import timedelta
import json
import pytest
from src.trading_runtime import expansion_stop as E, adaptive_episode_target as A, strategy_engine as S, v5_macd_episode as M
from tests.test_adaptive_episode_target import setup


def fixture():
    p, state, o, levels = setup()
    p['episode_management'].update(expansion_stop_enabled=True, adaptive_target_body_half_life=3.)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    d = state['v5_breakout_state']
    d.update(decision_levels=levels, crossed=[levels[1]], closed_atr=1., entry_close_location=1.)
    o = replace(o, source_timeframe='1s', evaluation_events=('bar_close',),
                price=102.2, bid=102.19, ask=102.21, bar_open=101., position_quantity=10.)
    return p, state, o, levels


def test_half_gap_promotes_previously_broken_band_on_later_close():
    p, state, o, levels = fixture()
    assert E.manage(o, p, state, 99.) == 99.
    state = json.loads(json.dumps(state))
    state['v5_breakout_state']['crossed'] = []
    later = replace(o, observed_at=o.observed_at+timedelta(seconds=1), price=103.1, bid=103.09, ask=103.11)
    assert E.manage(later, p, state, 99.) == pytest.approx(101.74)
    assert state['trailing_support_selection']['resistance']['upper'] == levels[1]['upper']
    assert state['trailing_support_selection']['gap_progress'] > .5
    assert E.manage(later, p, state, 104.) == 104.  # never loosen


def test_multi_band_strong_close_and_overlap_does_not_count_twice():
    p, state, o, levels = fixture()
    d = state['v5_breakout_state']
    d['crossed'] = levels[1:4]
    o = replace(o, price=105.2, bid=105.19, ask=105.21)
    assert E.manage(o, p, state, 99.) > 104.7
    assert state['trailing_support_selection']['distinct_breaks'] == 3
    p, state, o, levels = fixture()
    state['v5_breakout_state']['crossed'] = [levels[1], dict(levels[1], unified_level_id='duplicate')]
    assert E.manage(o, p, state, 99.) == 99.


def test_intrabar_touch_weak_close_and_swing_support():
    p, state, o, levels = fixture()
    assert E.manage(replace(o, evaluation_events=('market_data_update',)), p, state, 99.) == 99.
    state['v5_breakout_state'].update(crossed=levels[1:4], entry_close_location=.2)
    assert E.manage(replace(o, price=105.2), p, state, 99.) == 99.
    state['v5_breakout_state']['position_structure'] = {'established_support': {'boundary': 101.5}}
    later = replace(o, observed_at=o.observed_at+timedelta(seconds=1))
    assert E.manage(later, p, state, 99.) == 101.5


def test_recent_bodies_have_more_weight_and_legacy_remains_equal():
    p, _, _, _ = fixture()
    policy = p['episode_management']
    assert A.body_mean([1., 1., 1., 5.], policy) > 2.
    assert A.body_mean([5., 1., 1., 1.], policy) < 2.
    policy['adaptive_target_body_half_life'] = 0.
    assert A.body_mean([1., 1., 1., 5.], policy) == 2.


def test_shared_management_routes_stop_and_flat_clears_lifecycle():
    p, state, o, levels = fixture()
    state.update(active_stop=99., initial_stop=99.)
    state['v5_breakout_state'].update(crossed=levels[1:4], fill_stop_initialized=True)
    o = replace(o, price=105.2, bid=105.19, ask=105.21)
    assert M.manage(o, p, state) > 104.7
    assert state['trailing_support_selection']['source'] == 'promoted_resistance'
    M.observe(replace(o, position_quantity=0., observed_at=o.observed_at+timedelta(seconds=1)), p, state)
    assert 'expansion_stop' not in state['v5_breakout_state']


@pytest.mark.parametrize('key,value', [('adaptive_target_body_half_life', -1),
    ('expansion_stop_gap_fraction', 0), ('expansion_stop_minimum_breaks', 1),
    ('expansion_stop_atr_multiple', float('nan'))])
def test_invalid_settings(key, value):
    p, _, _, _ = fixture()
    p['episode_management'][key] = value
    with pytest.raises(ValueError):
        S.resolve_long_momentum_parameters(p, revision=47)
