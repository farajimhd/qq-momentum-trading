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


def test_gap_normalization_and_buffered_strict_entry():
    p, state, o = setup()
    assert M.gap_bps(replace(o, price=100, macd_line=.25, macd_signal=0)) == 25
    exact = replace(o, price=100, ask=100.01, bid=99.99, execution_vwap=99,
                    macd_line=.25, macd_signal=0)
    M.observe(replace(exact, observed_at=NOW+timedelta(seconds=1)), p, state)
    state['v5_breakout_state']['prior_max'] = 100
    assert M.select(exact, p, state)['reason'] == 'v5_period_high_not_reclaimed'
    assert M.select(replace(exact, price=100.15, macd_line=.3), p, state)['reason'] == 'v5_period_high_not_reclaimed'
    assert M.select(replace(exact, price=100.16, ask=100.17, bid=100.15, macd_line=.3), p, state)['reason'] == ''
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


def test_unavailable_macd_blocks_entry_without_fabricating_an_episode_reset():
    p, state, o = setup()
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=1), price=103.5, bar_open=104,
                      source_timeframe='1s', evaluation_events=('bar_close',)), p, state)
    missing = replace(o, observed_at=NOW+timedelta(seconds=1.1), macd_line=None)
    M.observe(missing, p, state)
    assert M.select(missing, p, state)['reason'] == 'v5_macd_gap_below_minimum'
    assert state['v5_breakout_state']['period_max'] == 104
    recovered = replace(o, observed_at=NOW+timedelta(seconds=1.2))
    M.observe(recovered, p, state)
    assert state['v5_breakout_state']['prior_max'] == 104
    assert M.select(recovered, p, state)['reason'] == 'v5_period_high_not_reclaimed'


def test_completed_macd_ignores_intrabar_dips_and_freezes_normalization_price():
    p, _, o = setup()
    p['macd_evaluation_mode'] = 'completed_1s'
    state = {}
    M.observe(o, p, state)
    assert not state['v5_breakout_state']['macd_open']
    closed = replace(o, observed_at=NOW+timedelta(seconds=1), price=100, bar_open=100,
                     macd_line=.25, macd_signal=0, source_timeframe='1s', evaluation_events=('bar_close',))
    M.observe(closed, p, state)
    assert state['v5_breakout_state']['macd_gap_bps'] == 25
    intrabar = replace(o, observed_at=NOW+timedelta(seconds=1.2), price=101,
                       macd_line=-1, macd_signal=0)
    M.observe(intrabar, p, state)
    assert state['v5_breakout_state']['prior_max'] == 100
    assert state['v5_breakout_state']['macd_gap_bps'] == 25
    assert M.acquisition_valid(replace(intrabar, execution_vwap=99), p, state)
    M.observe(replace(closed, observed_at=NOW+timedelta(seconds=2), macd_line=.24), p, state)
    assert state['v5_breakout_state']['prior_max'] == 0
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=2.1)), p, state)
    assert not state['v5_breakout_state']['macd_open']


def test_engine_uses_closed_macd_for_generic_rules_as_well_as_episode_gate():
    p, _, o = rejection_setup()
    p['macd_evaluation_mode'] = 'completed_1s'
    engine = S.LongMomentumStrategyEngine(revision=47)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p), o)
    assert not any(i.action == 'enter_long' for i in result.evaluation.intents)
    closed = replace(o, observed_at=NOW+timedelta(seconds=1), source_timeframe='1s',
                     evaluation_events=('bar_close',), bar_open=o.price)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=result.state), closed)
    trade = replace(o, observed_at=NOW+timedelta(seconds=1.1), price=103.5, bid=103.49,
                    ask=103.51, macd_line=-1, macd_signal=0)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=result.state), trade)
    assert any(i.action == 'enter_long' for i in result.evaluation.intents)
    evidence = result.state['confirmed_episode_macd']
    assert evidence['observed_at'] == closed.observed_at.isoformat()
    assert evidence['line'] == closed.macd_line


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
    assert not remaining.state.get('liquidation_origin_fill_role')
    result = engine.evaluate(remaining, replace(o, observed_at=NOW+timedelta(seconds=1),
                             position_quantity=98, average_price=103.3))
    assert not any(i.action == 'exit' for i in result.evaluation.intents)
    asyncio.run(strategy.on_order_group_update(SimpleNamespace(
        action='exit', assignment_id=assigned.assignment_id, fill_role='protective_stop',
        fill_incremental_quantity=1, state='partially_filled', updated_at=NOW+timedelta(seconds=2)),
        aggregate_position_quantity=97))
    stopped = strategy.assignments()[0]
    assert stopped.state['liquidation_origin_fill_role'] == 'protective_stop'
    assert stopped.state['last_exit_reason'] == 'protective_stop'
    assert stopped.status == S.AssignmentStatus.EXIT_PENDING


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


def rejection_setup():
    p, _, o = setup()
    p['v5_breakout_contract'] = V.MACD_REJECTION_CONTRACT
    p = S.resolve_long_momentum_parameters(p, revision=47)
    state = {}
    M.observe(o, p, state)
    M.observe(replace(o, observed_at=NOW+timedelta(milliseconds=1)), p, state)
    return p, state, o


def test_rejection_requires_held_contact_and_completed_close_strictly_below():
    p, state, o = rejection_setup()
    level = M.overhead(state['v5_breakout_state']['levels'], o.price, p)[0]
    def observe(seconds, price, closed=False, held=100, levels=None):
        current = replace(o, observed_at=NOW+timedelta(seconds=seconds), price=price,
                          position_quantity=held, source_timeframe='1s' if closed else '',
                          evaluation_events=('bar_close',) if closed else ('market_data_update',),
                          structural_resistance_levels=levels if levels is not None else o.structural_resistance_levels)
        M.observe(current, p, state)
        return state['v5_breakout_state']
    assert not observe(1, level['lower']-.01, True).get('resistance_rejection')
    assert not observe(2, level['price']).get('resistance_rejection')
    assert not observe(3, level['lower'], True).get('resistance_rejection')
    assert not observe(3.1, level['lower']-.01).get('resistance_rejection')
    # The touched band's witness survives its removal from the live book.
    rejected = observe(4, level['lower']-.01, True, levels=())['resistance_rejection']
    assert rejected['level']['unified_level_id'] == level['unified_level_id']
    assert rejected['touched_at'] == (NOW+timedelta(seconds=2)).isoformat()
    assert not observe(5, level['price'], held=0).get('resistance_rejection')


def test_completed_break_clears_rejection_attempt_and_pre_entry_touch_is_ignored():
    p, state, o = rejection_setup()
    level = M.overhead(state['v5_breakout_state']['levels'], o.price, p)[0]
    for seconds, price, held in [(1,level['price'],0), (2,level['lower']-.01,100),
                                  (3,level['price'],100), (4,level['upper']+.01,100),
                                  (5,level['lower']-.01,100)]:
        M.observe(replace(o, observed_at=NOW+timedelta(seconds=seconds), price=price,
                  position_quantity=held, source_timeframe='1s', evaluation_events=('bar_close',)), p, state)
        assert not state['v5_breakout_state'].get('resistance_rejection')


def test_rejection_boundary_ignores_float_noise_but_preserves_sub_tick_prices():
    level = {'unified_level_id': 'band', 'lower': 3.5300000000000002, 'upper': 3.55}
    _, _, o = rejection_setup()
    d = {}
    M.observe_rejection(replace(o, position_quantity=100, price=3.54), d, [level], False)
    M.observe_rejection(replace(o, position_quantity=100, price=3.53), d, [level], True)
    assert not d.get('resistance_rejection')
    M.observe_rejection(replace(o, position_quantity=100, price=3.5299), d, [level], True)
    assert d['resistance_rejection']['close_price'] == 3.5299


def test_new_contract_does_not_confirm_break_at_equal_float_upper_bound():
    p, state, o = rejection_setup()
    level = {'unified_level_id': 'band', 'lower': 3.53, 'upper': 3.5499999999999998}
    state['v5_breakout_state'].update(closed_levels=[level], closed_price=3.54)
    M.observe(replace(o, observed_at=NOW+timedelta(seconds=1), price=3.55,
                      source_timeframe='1s', evaluation_events=('bar_close',)), p, state)
    assert not state['v5_breakout_state']['crossed']


def test_new_engine_keeps_acquiring_on_entry_gate_lapse_then_exits_on_rejection():
    p, state, o = rejection_setup()
    engine = S.LongMomentumStrategyEngine(revision=47)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state), o)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.metadata['entry_completion_quote'] == 'ask'
    assert entry.resolved_execution_policy().envelope.persist_until_cancelled
    assert entry.resolved_execution_policy().envelope.maximum_buy_price is None
    assert str(entry.resolved_execution_policy().partial_fill_policy) == 'complete_remainder'
    for seconds, price, closed in [(1,103.6,False),(1.1,103.55,False),(2,103.49,True)]:
        result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=result.state,
                                 status=S.AssignmentStatus.MANAGING),
                                 replace(o, observed_at=NOW+timedelta(seconds=seconds), price=price,
                                         bid=price-.01, ask=price+.01, position_quantity=20,
                                         average_price=103.3, macd_line=.51, execution_vwap=104,
                                         source_timeframe='1s' if closed else '',
                                         evaluation_events=('bar_close',) if closed else ('market_data_update',)))
        assert not any(i.action == 'cancel_entry' for i in result.evaluation.intents)
        if not closed:
            assert not any(i.action == 'exit' for i in result.evaluation.intents)
    exit_intent = next(i for i in result.evaluation.intents if i.action == 'exit')
    assert exit_intent.reason == 'resistance_rejection'
    assert exit_intent.quantity == 20
    assert exit_intent.metadata['cancel_entry_acquisition']
    assert result.state['entry_acquisition_exit_latched']
