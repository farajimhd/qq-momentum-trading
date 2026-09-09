from dataclasses import replace
from datetime import timedelta
import pytest

from src.trading_runtime import v5_breakout as V, v5_macd_episode as M, strategy_engine as S
from tests.test_v5_breakout import parameters, market, resistance
from tests.test_long_momentum_strategy import assignment, NOW


def setup():
    p = parameters()
    p['v5_breakout_contract'] = M.CONTRACT
    p = S.resolve_long_momentum_parameters(p, revision=47)
    o = replace(market(), macd_line=1.0, macd_signal=.5)
    state = {}
    M.observe(o, p, state)
    M.observe(replace(o, observed_at=NOW + timedelta(milliseconds=1)), p, state)
    return p, state, o


def test_gap_normalization_threshold_and_equality_entry():
    p, state, o = setup()
    assert M.gap_bps(replace(o, price=100, macd_line=.25, macd_signal=0)) == 25
    exact = replace(o, price=100, ask=100.01, bid=99.99, execution_vwap=99,
                    macd_line=.25, macd_signal=0)
    M.observe(replace(exact, observed_at=NOW+timedelta(seconds=1)), p, state)
    state['v5_breakout_state']['prior_max'] = 100
    assert M.select(exact, p, state)['reason'] == ''
    assert not M.acquisition_valid(replace(exact, macd_line=.2499), p)
    assert M.select(replace(exact, price=99.99), p, state)['reason'] == 'v5_period_high_not_reclaimed'


def test_intrabar_reset_and_completed_body_only():
    p, state, o = setup()
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=1), price=103.5, bar_open=104,
                      source_timeframe='1s', evaluation_events=('bar_close',)), p, state)
    assert state['v5_breakout_state']['period_max'] == 104
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=1.1), bar_open=110), p, state)
    assert state['v5_breakout_state']['period_max'] == 104
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=1.2), macd_line=.51), p, state)
    assert state['v5_breakout_state']['period_max'] == 0
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=1.3)), p, state)
    assert state['v5_breakout_state']['prior_max'] == 0


def test_initial_second_target_and_outer_low_overrides_five_percent():
    p, state, o = setup()
    selected = M.select(o, p, state)
    assert selected['target_selection']['level']['price'] == 104
    support = dict(resistance(90), side=1, scale='major')
    selected = M.select(replace(o, structural_support_levels=(support,)), p, state)
    assert selected['stop'] < 90  # farther than five percent, intentionally
    assert selected['stop_selection']['source'] == 'outer_swing_low'
    state['v5_breakout_state']['decision_levels'] = [resistance(104)]
    assert M.select(o, p, state)['reason'] == 'v5_second_target_unavailable'


def test_touch_and_intrabar_break_do_not_advance_protection():
    p, state, o = setup()
    state.update(active_stop=98, initial_stop=98)
    for offset, price, closed in [(1,103.4,True),(1.2,104,False),(2,103.6,True)]:
        current = replace(o, observed_at=NOW+timedelta(seconds=offset), price=price,
                          position_quantity=100, average_price=103.3,
                          source_timeframe='1s' if closed else '',
                          evaluation_events=('bar_close',) if closed else ('market_data_update',))
        M.observe(current, p, state)
        assert M.manage(current, p, state) == 98
    current = replace(current, observed_at=NOW+timedelta(seconds=3), price=103.8)
    M.observe(current, p, state)
    assert M.manage(current, p, state) > 98


def test_average_gap_target_is_position_scoped_unique_and_upward_only():
    p, state, o = setup()
    levels = [dict(resistance(x), lower=x-.01, upper=x+.01) for x in (100,102,104,105,106,108)]
    d = state['v5_breakout_state']
    d.update(decision_levels=levels, crossed=[levels[1]], fill_stop_initialized=True)
    state.update(active_stop=95, initial_stop=95, structural_profit_targets=[103.98])
    current = replace(o, price=102.1, bid=102.09, ask=102.11, position_quantity=100, average_price=100.1)
    M.manage(current, p, state)
    selected = d['pending_target']
    assert selected['average_gap'] == pytest.approx(1.5)
    assert selected['level']['price'] == 105  # tie goes to the closer lower resistance
    count = len(d['position_gaps'])
    M.manage(current, p, state)
    assert len(d['position_gaps']) == count
    state['structural_profit_targets'] = [110]
    d.pop('pending_target')
    M.manage(current, p, state)
    assert 'pending_target' not in d
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=10), position_quantity=0), p, state)
    assert 'position_gaps' not in state['v5_breakout_state']


def test_real_engine_cancels_acquisition_without_forming_exit():
    p, state, o = setup()
    engine = S.LongMomentumStrategyEngine(revision=47)
    entered = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state), o)
    assert any(i.action == 'enter_long' for i in entered.evaluation.intents)
    held = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=entered.state,
                           status=S.AssignmentStatus.MANAGING),
                           replace(o, observed_at=NOW+timedelta(seconds=1), position_quantity=100,
                                   average_price=103.3, macd_line=.51))
    assert any(i.action == 'cancel_entry' for i in held.evaluation.intents)
    assert not any(i.action == 'exit' for i in held.evaluation.intents)


def test_partial_target_fill_does_not_order_urgent_liquidation():
    import asyncio
    from types import SimpleNamespace
    p, state, o = setup()
    engine = S.LongMomentumStrategyEngine(revision=47)
    entered = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state), o)
    assigned = assignment(strategy_revision=47, parameters=p, state=entered.state,
                          status=S.AssignmentStatus.MANAGING)
    strategy = S.AssignedLongMomentumStrategy([assigned])
    asyncio.run(strategy.on_order_group_update(SimpleNamespace(
        action='exit', assignment_id=assigned.assignment_id, fill_role='profit_target',
        fill_incremental_quantity=2, slice_id='target', state='partially_filled', updated_at=NOW),
        aggregate_position_quantity=98))
    remaining = strategy.assignments()[0]
    assert remaining.status == S.AssignmentStatus.MANAGING
    assert not remaining.state.get('profit_target_liquidation_required')
    result = engine.evaluate(remaining, replace(o, observed_at=NOW+timedelta(seconds=1),
                             position_quantity=98, average_price=103.3))
    assert not any(i.action == 'exit' for i in result.evaluation.intents)


def test_trade_at_close_cannot_erase_completed_break_witness():
    p, state, o = setup()
    first = replace(o, observed_at=NOW+timedelta(seconds=1), price=103.4,
                    source_timeframe='1s', evaluation_events=('bar_close',), position_quantity=100)
    M.observe(first, p, state)
    removed = tuple(r for r in o.structural_resistance_levels if r['price'] != 103.6)
    trade = replace(o, observed_at=NOW+timedelta(seconds=2), price=103.8,
                    structural_resistance_levels=removed, position_quantity=100)
    M.observe(trade, p, state)
    assert not state['v5_breakout_state']['crossed']
    M.observe(replace(trade, source_timeframe='1s', evaluation_events=('bar_close',)), p, state)
    assert any(r['price'] == 103.6 for r in state['v5_breakout_state']['crossed'])
