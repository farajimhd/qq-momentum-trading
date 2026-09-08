from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import pytest

from src.trading_runtime import v5_breakout as V, strategy_engine as S
from tests.test_swing_evidence_strategy import policy, obs, entered
from tests.test_swing_momentum import level
from tests.test_long_momentum_strategy import assignment, NOW


def parameters():
    p = policy()
    p['v5_breakout_contract'] = V.CONTRACT
    return S.resolve_long_momentum_parameters(p, revision=47)


def resistance(price, stamp=None):
    return dict(level(price, -1, stamp=stamp), book_version='causal-swing-closing-book-5',
                selection_score=40, p_norm=None)


def market(offset=0, price=103.3, **kw):
    return replace(obs(), observed_at=NOW+timedelta(seconds=offset), price=price,
        bar_open=103.1, bid=price-.01, ask=price+.01, execution_vwap=102.,
        structural_session_high=103.5,
        structural_resistance_levels=tuple(resistance(p) for p in (103.2,103.6,104.,105.)),
        source_timeframe='', evaluation_events=('market_data_update',), **kw)


def ready():
    p, state = parameters(), {'entry_body_reference':dict(open=103.1, close=103.15,
        end=NOW.timestamp(), expires=NOW.timestamp()+1)}
    for offset, price in ((0,103.1),(.2,103.15),(.4,103.3)):
        V.observe(market(offset,price),p,state)
    return p,state


def test_entry_broken_band_body_direction_and_target():
    p,state=ready()
    selected=V.select(market(.4),p,state)
    assert selected['reason']=='',selected
    assert selected['stop'] < 103.19
    assert selected['target']==pytest.approx(103.98)
    assert V.select(market(.4,103.1),p,state)['reason']=='v5_previous_body_not_broken'
    assert V.select(replace(market(.4),execution_vwap=104),p,state)['reason']=='v5_price_not_above_vwap'
    data=state['v5_breakout_state']
    data['history']=[(NOW.timestamp(),104),(NOW.timestamp()+.2,103.5),(NOW.timestamp()+.4,103.3)]
    assert V.select(market(.4),p,state)['reason']=='v5_direction_not_upward'


def test_no_future_or_newly_appearing_crossing_and_no_sparse_direction():
    p,state=ready()
    future=resistance(103.2, NOW.timestamp()+2)
    assert V.rows(replace(market(),structural_resistance_levels=(future,)),p)==[]
    state={'entry_body_reference':state['entry_body_reference']}
    V.observe(replace(market(0,103.1),structural_resistance_levels=()),p,state)
    V.observe(market(.2,103.3),p,state)
    assert not state['v5_breakout_state'].get('breakout')
    V.observe(market(.8,103.4),p,state)
    assert V.select(market(.8,103.4),p,state)['reason']=='v5_direction_not_upward'


def test_real_engine_entry_and_no_macd_exit_and_pending_exit_guard():
    p,state=ready()
    e=S.LongMomentumStrategyEngine(revision=47)
    result=e.evaluate(assignment(strategy_revision=47,parameters=p,state=state),market(.4))
    assert entered(result),[s.reason for s in result.evaluation.signals]
    assert result.state['structural_profit_targets']==pytest.approx([103.98])
    json.dumps(result.state)
    held=e.evaluate(assignment(strategy_revision=47,parameters=p,state=result.state,
        status=S.AssignmentStatus.MANAGING),replace(market(.5),position_quantity=100,average_price=103.3,
        macd_line=-.3,macd_signal=-.2))
    assert not any(i.action=='exit' for i in held.evaluation.intents)
    blocked=e.evaluate(assignment(strategy_revision=47,parameters=p,state=state),
        replace(market(.4),pending_exit_quantity=1))
    assert not entered(blocked)


def test_adaptive_target_and_new_resistance_stop():
    p,state=ready()
    selected=V.select(market(.4),p,state)
    state.update(v5_entry_selection=selected,active_stop=selected['stop'],high_water_price=103.3)
    V.manage(replace(market(.5),position_quantity=100),p,state)
    move=state['v5_breakout_state']['move']
    move.update(sum=.5,count=1)
    m=replace(market(.6,103.7),position_quantity=100)
    V.observe(m,p,state)
    stop=V.manage(m,p,state)
    assert stop>selected['stop']
    # Body average skips 104; without a second level beyond the reference,
    # retain the existing target rather than inventing a future resistance.
    assert not state['v5_breakout_state'].get('pending_target')
    state['active_stop']=stop
    state['high_water_price']=103.7
    new=resistance(103.68,NOW.timestamp()+.7)
    m=replace(market(.8,103.5),position_quantity=100,
        structural_resistance_levels=(*m.structural_resistance_levels,new))
    V.observe(m,p,state)
    assert V.manage(m,p,state)>m.price  # engine must exit immediately
    json.dumps(state)


def test_completed_body_average_excludes_partial_and_duplicate_candles():
    p,state=ready()
    state.update(v5_entry_selection=V.select(market(.4),p,state),active_stop=103.,high_water_price=104.)
    V.manage(replace(market(.5),position_quantity=100),p,state)
    for t in (1,2,2):
        m=replace(market(t,103.4),position_quantity=100,source_timeframe='1s',evaluation_events=('bar_close',),bar_open=103.2)
        V.observe(m,p,state)
    move=state['v5_breakout_state']['move']
    assert move['count']==1
    assert move['sum']==pytest.approx(.2)


def test_engine_replaces_both_stop_and_target_then_exits_on_new_ceiling():
    p,state=ready()
    e=S.LongMomentumStrategyEngine(revision=47)
    first=e.evaluate(assignment(strategy_revision=47,parameters=p,state=state),market(.4))
    a=assignment(strategy_revision=47,parameters=p,state=first.state,status=S.AssignmentStatus.MANAGING)
    m=replace(market(.5,103.5),position_quantity=100,average_price=103.3)
    held=e.evaluate(a,m)
    m=replace(market(.6,103.7),position_quantity=100,average_price=103.3)
    advanced=e.evaluate(replace(a,state=held.state),m)
    actions={i.action for i in advanced.evaluation.intents}
    assert 'replace_profit_target' in actions, actions
    assert advanced.state['active_stop'] > first.state['active_stop']
    assert advanced.state['structural_profit_targets']==pytest.approx([104.98])
    new=resistance(103.68,NOW.timestamp()+.7)
    m=replace(market(.8,103.5),position_quantity=100,average_price=103.3,
        structural_resistance_levels=(*m.structural_resistance_levels,new))
    stopped=e.evaluate(replace(a,state=advanced.state),m)
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in stopped.evaluation.intents)


def test_body_size_skips_nearby_targets_and_retains_lower_stop():
    p,state=ready()
    rs=[resistance(x) for x in (103.6,104,104.2,104.8,105.2)]
    broken=resistance(103.2)
    assert V.target(rs,broken,0,p)['level']['price']==104
    assert V.target(rs,broken,.9,p)['level']['price']==104.8
    state.update(v5_entry_selection=V.select(market(.4),p,state),active_stop=103.15,high_water_price=103.3)
    assert V.manage(replace(market(.5),position_quantity=100),p,state)==103.15


def test_top_three_only_and_reentry_requires_new_crossing():
    p,state=ready()
    data=state['v5_breakout_state']
    data.pop('breakout')
    data['sample']=(NOW.timestamp()+.4,103.1)
    rs=tuple(resistance(x) for x in (103.2,103.3,103.4,103.5,104,105))
    data['levels']=data['prior_levels']=list(rs)
    V.observe(replace(market(.6,103.25),structural_resistance_levels=rs,structural_session_high=103.52),p,state)
    assert not data.get('breakout')
    assert not state['v5_breakout_state'].get('breakout')  # fourth below HOD
    p,state=ready()
    state['v5_breakout_state'].pop('breakout')
    V.observe(market(.5,103.35),p,state)
    assert not state['v5_breakout_state'].get('breakout')  # already above != new break
    V.observe(market(.6,103.1),p,state)
    V.observe(market(.8,103.35),p,state)
    assert state['v5_breakout_state']['breakout']['level']['price']==103.2
