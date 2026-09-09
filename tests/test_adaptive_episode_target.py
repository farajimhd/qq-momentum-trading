from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from src.trading_runtime import adaptive_episode_target as A, v5_macd_episode as M, strategy_engine as S
from tests.test_v5_macd_episode import rejection_setup
from tests.test_v5_breakout import resistance
from tests.test_long_momentum_strategy import assignment


def setup():
    p, state, o = rejection_setup()
    p['episode_management'] = dict(position_structure_enabled=True, adaptive_target_enabled=True)
    p['protection_profile_catalog'] = {'test': {'profile_id': 'test', 'slices': [
        {'slice_id': 'target', 'quantity_fraction': 1., 'stop': {'rule_type': 'fixed_price'}}]}}
    p['phase_policy']['initial_entry'] = dict(order_intent=dict(protection_profile='test',
        execution_policy='adaptive_urgent', partial_fill_policy='complete_remainder'))
    p = S.resolve_long_momentum_parameters(p, revision=47)
    levels = [dict(resistance(x), lower=x-.01, upper=x+.01) for x in (100, 102, 104, 105, 106, 108, 110, 114)]
    o = replace(o, structural_resistance_levels=tuple(levels))
    return p, state, o, levels


def test_expansion_uses_executable_resistance_above_distance_and_sparse_fallback():
    p, state, o, levels = setup()
    d = state['v5_breakout_state']
    d['adaptive_target'] = dict(bodies=[2., 2., 2.], gaps=[['a->b', 1.]], episode=1, closed_at=1)
    above = levels[3:]
    selected = A.select(above, d, p)
    assert selected['average_bullish_body'] == 2
    assert selected['target_reference'] == pytest.approx(108.99)
    assert selected['level']['price'] == 110
    assert not selected['book_limited']
    d['adaptive_target']['bodies'] = [20.]
    assert A.select(above, d, p)['level']['price'] == 114
    assert A.select(above, d, p)['book_limited']


def test_all_breaks_sampled_once_and_target_advances_only_at_close():
    p, state, o, levels = setup()
    state.update(active_stop=95., initial_stop=95., structural_profit_targets=[103.98])
    before = replace(o, price=100.1, bid=100.09, ask=100.11, position_quantity=100, average_price=100.)
    M.observe(replace(before, observed_at=o.observed_at+timedelta(seconds=1),
                      source_timeframe='1s', evaluation_events=('bar_close',), bar_open=100.), p, state)
    tick = replace(before, observed_at=o.observed_at+timedelta(seconds=1.5), price=105.2, bid=105.19, ask=105.21)
    M.observe(tick, p, state)
    M.manage(tick, p, state)
    assert 'pending_target' not in state['v5_breakout_state']
    closed = replace(tick, observed_at=o.observed_at+timedelta(seconds=2),
                     source_timeframe='1s', evaluation_events=('bar_close',), bar_open=100.1)
    M.observe(closed, p, state)
    d = state['v5_breakout_state']
    assert len(d['crossed']) == 3
    M.manage(closed, p, state)
    selected = d['pending_target']
    assert len(selected['broken_level_ids']) == 3
    assert selected['level']['price'] == 114
    assert selected['gap_samples'] == 4
    # Exhaustion prevents a replacement but does not remove the working target
    # or suppress the stop ratchet for the same completed resistance breaks.
    d.pop('pending_target')
    d['adaptive_target']['paused'] = True
    assert M.manage(closed, p, state) > 95.
    assert 'pending_target' not in d
    assert state['structural_profit_targets'] == [103.98]
    d['adaptive_target']['paused'] = False
    M.manage(closed, p, state)
    saved = json.dumps(d['adaptive_target'])
    M.observe(closed, p, state)
    assert json.dumps(d['adaptive_target']) == saved
    state = json.loads(json.dumps(state))
    state['structural_profit_targets'] = [120.]
    state['v5_breakout_state'].pop('pending_target')
    M.manage(closed, p, state)
    assert 'pending_target' not in state['v5_breakout_state']


def test_bounded_samples_contraction_stalls_and_episode_reset():
    p, state, o, levels = setup()
    policy = p['episode_management']
    policy.update(adaptive_target_body_window=3, adaptive_target_gap_window=2)
    d = dict(macd_open=True, episode_started_at=1, crossed=levels[:3], decision_levels=levels)
    def feed(second, body, closed=True):
        A.observe(replace(o, observed_at=o.observed_at+timedelta(seconds=second),
                          price=103.+body, bar_open=103., source_timeframe='1s' if closed else '',
                          evaluation_events=('bar_close',) if closed else ('market_data_update',)), d, policy)
    feed(1, 2.)
    feed(2, .1)
    feed(2.5, 9., False)
    assert not d['adaptive_target']['paused']
    feed(3, .1)
    assert d['adaptive_target']['paused']
    feed(4, 3.)
    assert not d['adaptive_target']['paused']
    assert len(d['adaptive_target']['bodies']) == 3
    assert len(d['adaptive_target']['gaps']) == 2
    d['position_structure'] = {'resistance': {'upper': 110., 'source': 'v5', 'level_id': 'r'}}
    feed(5, 3.)
    feed(6, 3.)
    assert d['adaptive_target']['paused']
    d['macd_open'] = False
    feed(7, 3.)
    assert 'adaptive_target' not in d
    d.update(macd_open=True, episode_started_at=2, crossed=[], position_structure={})
    feed(8, .2)
    assert len(d['adaptive_target']['bodies']) == 1
    assert not d['adaptive_target']['gaps']


def test_engine_initial_bracket_uses_adaptive_target_and_legacy_is_opt_out():
    p, state, o, levels = setup()
    d = state['v5_breakout_state']
    d.update(decision_levels=levels, levels=levels, adaptive_target={'bodies':[2.], 'gaps':[]})
    original = deepcopy(p)
    original['episode_management']['adaptive_target_enabled'] = False
    legacy = M.select(o, original, state)
    selected = M.select(o, p, state)
    assert selected['target'] > legacy['target']
    engine = S.LongMomentumStrategyEngine(revision=47)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state), o)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.profit_target_price == selected['target']
    assert all(s.profit_target_price == selected['target'] for s in entry.resolved_protection_profile().slices)


@pytest.mark.parametrize('key,value', [('adaptive_target_body_window', 0), ('adaptive_target_gap_window', 121),
    ('adaptive_target_body_multiple', float('nan')), ('adaptive_target_contraction_ratio', 1.1)])
def test_invalid_configuration_rejected(key, value):
    p, _, _, _ = setup()
    p['episode_management'][key] = value
    with pytest.raises(ValueError):
        S.resolve_long_momentum_parameters(p, revision=47)
