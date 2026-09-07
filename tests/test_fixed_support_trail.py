from dataclasses import replace
from datetime import timedelta

from src.trading_runtime import strategy_engine as S
from tests.test_hod_resistance_entry_stop import policy, observation
from tests.test_long_momentum_strategy import NOW, assignment
from tests.test_point_structure_strategy import row


def configured():
    p = policy()
    p.update(macd_histogram_gate_bps=5, require_completed_entry_candle=True)
    p['structural_entry']['entry_level_ordinal_below_high'] = 1
    p['protection']['stop'].update(method='ordinal_qualified_support', support_level_ordinal=1,
        require_qualified_support=True, cap_initial_stop_distance=False, structure_buffer_bps=0)
    p['protection']['trailing'].update(enabled=True, mode='fixed_support_distance', activation_gain_pct=0)
    p['protection']['profit_ladder'].update(fixed_at_entry=True, target_level_ordinal=3)
    return p


def market(price=103.3):
    sources = {key: {'value': value, 'observed_at': NOW.isoformat()} for key, value in {
        'indicator.flow_structure.score@100ms': .7, 'indicator.flow_structure.confidence@100ms': .8,
        'indicator.macd.line@5s': .4, 'indicator.macd.signal@5s': .2,
        'indicator.macd.histogram@5s': .2}.items()}
    return replace(observation(price), bar_open=price-.1, macd_line=.4, macd_signal=.2,
        source_values=sources, structural_support_levels=(row(102,1),row(101,1)))


def test_entry_uses_nearest_support_and_third_resistance():
    p=configured(); obs=market()
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p),obs)
    entry=next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert entry.invalidation_price == 102
    assert result.state['trailing_amount'] == 1.3
    assert result.state['structural_profit_targets'] == [106]
    assert S._initial_stop(replace(obs,structural_support_levels=()),p,None,side='long') == 0


def test_resolved_policy_preserves_distant_support_without_price_cap():
    p=S.resolve_long_momentum_parameters(configured(),revision=47)
    assert not p['protection']['stop']['cap_initial_stop_distance']
    obs=replace(market(),structural_support_levels=(row(80,1),))
    assert S._initial_stop(obs,p,None,side='long')==80
    assert S._trailing_amount(obs,p,stop=80)==23.3


def test_trail_distance_is_frozen_and_target_never_advances():
    p=configured(); engine=S.LongMomentumStrategyEngine(revision=47)
    state={'entry_reference_price':103.3,'initial_stop':102,'active_stop':102,'trailing_amount':1.3,
        'high_water_price':104.3,'low_water_price':103.3,'structural_profit_targets':[106]}
    obs=market(104.3)
    assert S._ratcheted_stop(obs,p,state,side='long') == 103
    state['active_stop']=103
    assert S._ratcheted_stop(replace(obs,price=103.5,structural_support_levels=()),p,state,side='long') == 103
    assert state['trailing_amount']==1.3
    assert engine._structural_target_replacement_result(assignment(parameters=p),obs,p,state,side='long',stop=103) is None
    assert state['structural_profit_targets']==[106]


def test_trailing_update_reaches_execution_as_stop_replacement():
    p=configured(); engine=S.LongMomentumStrategyEngine(revision=47)
    result=engine.evaluate(assignment(strategy_revision=47,parameters=p),market())
    state=dict(result.state,entry_at=(NOW-timedelta(seconds=2)).isoformat())
    obs=replace(market(104.3),position_quantity=100,average_price=103.3)
    managed=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=state,status=S.AssignmentStatus.MANAGING),obs)
    stop=next(i for i in managed.evaluation.intents if i.action=='replace_protective_stop')
    assert stop.invalidation_price==103
    assert not any(i.action=='replace_profit_target' for i in managed.evaluation.intents)
