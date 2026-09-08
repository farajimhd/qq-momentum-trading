from dataclasses import replace
from datetime import timedelta
import json

import pytest

from src.trading_runtime import v5_breakout as V, strategy_engine as S
from tests.test_v5_breakout import parameters, market, resistance
from tests.test_long_momentum_strategy import NOW, assignment


def setup():
    p = parameters()
    p['v5_breakout_contract'] = V.HOD_CONTRACT
    p = S.resolve_long_momentum_parameters(p, revision=47)
    state = {'entry_body_reference': dict(open=103.1, close=103.15,
              end=NOW.timestamp(), expires=NOW.timestamp()+1)}
    m = replace(market(), structural_session_high=104.2,
                structural_resistance_levels=tuple(resistance(x) for x in
                                                   (103.2, 103.5, 103.8, 104, 105, 106)))
    V.observe(m, p, state)
    m = replace(m, observed_at=NOW+timedelta(seconds=.1))
    V.observe(m, p, state)
    selected = V.select(m, p, state)
    assert not selected['reason'], selected
    state.update(v5_entry_selection=selected, active_stop=selected['stop'])
    return p, state, m


def step(p, state, m, t, price, closed=False, **changes):
    m = replace(m, observed_at=NOW+timedelta(seconds=t), price=price,
                bid=price-.01, ask=price+.01, position_quantity=100,
                evaluation_events=('bar_close',) if closed else ('market_data_update',),
                source_timeframe='1s' if closed else '', **changes)
    V.observe(m, p, state)
    state['active_stop'] = V.manage(m, p, state)
    return m


def test_entry_without_crossing_direction_ceiling_or_positive_macd():
    p, state, m = setup()
    m = replace(m, price=103.7, bid=103.69, ask=103.71, execution_vwap=103.4)
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), m)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.invalidation_price == pytest.approx(98.51)
    assert entry.profit_target_price == pytest.approx(103.98)
    snapshot = entry.metadata['unified_structural_trigger']['current_snapshot']
    assert len(snapshot['levels']) == 4
    assert all(r['upper'] <= snapshot['session_high'] for r in snapshot['levels'])
    assert m.macd_signal < m.macd_line < 0
    json.dumps(result.state)


def test_only_completed_close_advances_r3_then_r2_and_no_body_average():
    p, state, m = setup()
    initial = state['active_stop']
    step(p, state, m, 1, 103.3, True)
    step(p, state, m, 1.5, 103.7)
    assert state['active_stop'] == initial
    assert 'pending_target' not in state['v5_breakout_state']
    step(p, state, m, 2, 103.7, True)
    assert state['active_stop'] == V.below(resistance(103.2), p)
    assert state['v5_breakout_state'].pop('pending_target')['price'] == pytest.approx(104.98)
    step(p, state, m, 3, 103.9, True)
    assert state['v5_breakout_state'].pop('pending_target')['price'] == pytest.approx(105.98)
    step(p, state, m, 3, 103.9, True)
    assert 'pending_target' not in state['v5_breakout_state']
    assert 'move' not in state['v5_breakout_state']


def test_hod_and_new_rank_do_not_create_a_break():
    p, state, m = setup()
    step(p, state, m, 1, 103.7, True)
    changed = tuple(resistance(x) for x in (103.1, 103.3, 103.4, 104, 105, 106))
    step(p, state, m, 1.5, 103.7, structural_resistance_levels=changed,
         structural_session_high=106.2)
    assert state['v5_breakout_state']['entry_ranked'][0]['upper'] == pytest.approx(104.01)
    step(p, state, m, 2, 103.7, True, structural_resistance_levels=changed,
         structural_session_high=106.2)
    assert state['v5_breakout_state']['stages']['phase'] == 0
    assert 'pending_target' not in state['v5_breakout_state']


def test_new_local_resistance_only_ratchets_after_target_advance():
    p, state, m = setup()
    step(p, state, m, 1, 103.3, True)
    step(p, state, m, 2, 103.7, True)
    new = resistance(103.6, NOW.timestamp()+2.5)
    step(p, state, m, 3, 103.7, structural_resistance_levels=m.structural_resistance_levels+(new,))
    assert state['active_stop'] == V.below(new, p)
    step(p, state, m, 4, 103.6)
    assert state['active_stop'] == V.below(new, p)


def test_missing_four_is_explicit_and_macd_close_does_not_exit():
    p, state, m = setup()
    state['v5_breakout_state']['entry_ranked'].pop()
    assert V.select(m,p,state)['reason'] == 'v5_four_resistances_below_hod_unavailable'
    p, state, m = setup()
    engine = S.LongMomentumStrategyEngine(revision=47)
    result = engine.evaluate(assignment(strategy_revision=47,parameters=p,state=state),m)
    held = engine.evaluate(assignment(strategy_revision=47,parameters=p,state=result.state,
        status=S.AssignmentStatus.MANAGING),replace(m,observed_at=NOW+timedelta(seconds=.2),
        position_quantity=100,average_price=103.3,macd_line=-.3,macd_signal=-.2))
    assert not any(i.action == 'exit' for i in held.evaluation.intents)


def test_contract_removes_inherited_experiments():
    p = parameters()
    p.update(v5_breakout_contract=V.HOD_CONTRACT, normalized_macd_threshold_bps=30,
             require_positive_macd_signal_for_entry=True, local_swing_management=True,
             broken_level_stop_only=True, require_breakout_reset=True)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    assert 'normalized_macd_threshold_bps' not in p
    for key in ('local_swing_management','broken_level_stop_only','require_breakout_reset',
                'require_positive_macd_signal_for_entry','require_completed_entry_candle'):
        assert p[key] is False
    assert p['momentum_management']['macd_backstop']['enabled'] is False
    assert p['entry_candle_confirmation']['enabled'] is False
    assert p['phase_policy']['exit']['rule_sets'] == []


def test_engine_emits_stop_and_target_replacements_on_r3_close():
    p, state, m = setup()
    engine = S.LongMomentumStrategyEngine(revision=47)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state), m)
    for t, price in ((1, 103.3), (2, 103.7)):
        current = replace(m, observed_at=NOW+timedelta(seconds=t), price=price,
                          bid=price-.01, ask=price+.01, position_quantity=100, average_price=103.3,
                          source_timeframe='1s', evaluation_events=('bar_close',))
        result = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=result.state,
                                            status=S.AssignmentStatus.MANAGING), current)
    assert result.state['active_stop'] == V.below(resistance(103.2), p)
    target_intent = next(i for i in result.evaluation.intents if i.action == 'replace_profit_target')
    assert target_intent.profit_target_price == pytest.approx(104.98)
    assert target_intent.metadata['v5_gate_evidence']['ladder_stage']['phase'] == 1


def test_three_resistances_use_vwap_as_r4_and_entry_snapshot():
    p,state,m=setup();p['v5_hod_vwap_fallback']=True
    refs=tuple(resistance(x) for x in (103.5,103.8,104,105,106))
    m=replace(m,structural_resistance_levels=refs)
    V.observe(m,p,state)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47,parameters=p,state=state),m)
    entry=next(i for i in result.evaluation.intents if i.action=='enter_long')
    rows=entry.metadata['unified_structural_trigger']['current_snapshot']['levels']
    assert rows[-1]['reference_kind']=='vwap' and rows[-1]['entry_boundary']==102
    assert entry.invalidation_price==pytest.approx(98.13)


def test_vwap_fallback_requires_three_and_vwap_below_r3():
    p,state,m=setup();p['v5_hod_vwap_fallback']=True
    for prices,vwap in (((103.8,104),102),((103.5,103.8,104),103.6)):
        current=replace(m,execution_vwap=vwap,structural_resistance_levels=tuple(resistance(x) for x in prices))
        V.observe(current,p,state);V.observe(current,p,state)
        assert len(state['v5_breakout_state']['entry_ranked'])<4


def test_pending_rows_only_consumed_by_new_candidate():
    p,state,m=setup()
    row=dict(resistance(103.2),lifecycle='awaiting_retest',retained_qualified_resistance=True)
    current=replace(m,structural_resistance_levels=(row,))
    assert not V.rows(current,p)
    p['v5_hod_vwap_fallback']=True
    assert V.rows(current,p)[0]['lifecycle']=='awaiting_retest'
