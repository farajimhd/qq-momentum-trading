from dataclasses import replace
from datetime import timedelta
import pytest
from src.trading_runtime import v5_breakout as V, v5_staged
from tests.test_v5_breakout import parameters, market, resistance
from tests.test_long_momentum_strategy import NOW


def setup():
    p=parameters()
    p['v5_breakout_contract']=V.STAGED_CONTRACT
    V.configure(p)
    refs=[resistance(x) for x in (104,103.8,103.5,103.2)]
    state={'entry_body_reference':dict(open=103.1,close=103.15,end=NOW.timestamp(),expires=NOW.timestamp()+1)}
    for t,price in ((0,103.1),(.2,103.15),(.4,103.3)):
        m=replace(market(t,price),structural_session_high=104.2,
                  structural_resistance_levels=tuple(refs+[resistance(105),resistance(106)]))
        V.observe(m,p,state)
    selected=V.select(m,p,state)
    assert not selected['reason'],selected
    state.update(v5_entry_selection=selected,active_stop=selected['stop'],high_water_price=103.3)
    return p,state,m


def test_r4_entry_r3_stop_and_target_above_frozen_r1():
    p,state,m=setup()
    assert state['active_stop']==pytest.approx(98.13)
    assert state['v5_entry_selection']['target']==pytest.approx(103.78)
    assert V.select(replace(m,price=103.6),p,state)['reason']=='v5_entry_not_below_r3'
    state['high_water_price']=103.7
    stop=V.manage(replace(m,price=103.7,position_quantity=100),p,state)
    assert stop==V.below(resistance(103.2),p)
    assert state['v5_breakout_state']['pending_target']['price']==pytest.approx(104.98)
    assert state['v5_breakout_state']['stages']['phase']=='r3'


def test_r1_waits_for_prior_known_local_top_then_restarts_average():
    p,state,m=setup()
    V.manage(replace(m,price=104.1,position_quantity=100),p,state)
    data=state['v5_breakout_state']
    stage=data['stages']
    assert stage['phase']=='waiting_local_top'
    stage.update(total=.2,count=2)
    new=resistance(104.5,NOW.timestamp()+2)
    data['levels'].append(new)
    state['high_water_price']=104.6
    # Discover the top first; its existence cannot be backdated to this bar.
    observation=replace(m,observed_at=NOW+timedelta(seconds=3),price=104.3,position_quantity=100)
    V.manage(observation,p,state)
    assert data['stages']['top']['price']==104.5
    observation=replace(observation,observed_at=observation.observed_at+timedelta(seconds=1),
                        source_timeframe='1s',evaluation_events=('bar_close',),bar_open=104.3,price=104.7)
    V.manage(observation,p,state)
    stage=data['stages']
    assert stage['phase']=='adaptive'
    assert stage['count']==1 and stage['total']==pytest.approx(.4)
    V.manage(observation,p,state)
    assert data['stages']['count']==1


def test_entry_snapshot_projection_keeps_four_references():
    from src.backend.trading_runtime_service import _compact_strategy_gate_snapshot
    p,state,m=setup()
    projected=_compact_strategy_gate_snapshot({'unified_structural_trigger':{'current_snapshot':{
        'frozen_at_entry':True,'levels':state['v5_entry_selection']['references'],'session_high':104.2}}})
    snapshot=projected['unified_structural_trigger']['current_snapshot']
    assert snapshot['frozen_at_entry']
    assert len(snapshot['levels'])==4


def test_production_engine_emits_staged_entry_and_frozen_references():
    from src.trading_runtime.strategy_engine import LongMomentumStrategyEngine
    from tests.test_long_momentum_strategy import assignment
    p,state,m=setup()
    result=LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47,parameters=p,state=state),m)
    intent=next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert intent.invalidation_price==pytest.approx(98.13)
    assert intent.profit_target_price==pytest.approx(103.78)
    assert len(intent.metadata['unified_structural_trigger']['current_snapshot']['levels'])==4
