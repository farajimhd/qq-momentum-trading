from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import pytest
from src.trading_runtime import swing_momentum as M, strategy_engine as S
from tests.test_swing_evidence_strategy import policy,obs,entered
from tests.test_long_momentum_strategy import assignment,NOW


def level(price=100,side=1,score=.2,stamp=None):
    stamp=stamp or NOW.timestamp()-5
    return dict(book_version='causal-swing-closing-book-4',lifecycle='active',side=side,
                unified_level_id=str(price),price=price,lower=price-.01,upper=price+.01,
                p_norm=score,created_at_ms=stamp*1000,confirmed_at_ms=stamp*1000)


def test_exact_threshold_future_and_old_contract_rejected():
    levels=[level(),level(101,score=.199),level(102,stamp=NOW.timestamp()+1),dict(level(99),book_version='causal-swing-closing-book-3')]
    assert M.context(levels,103,NOW.timestamp())['support']['id']=='100'


def test_bands_room_and_green_are_entry_conditions():
    ctx=M.context([level(103,-1)],102.9,NOW.timestamp())
    assert M.entry(ctx,102.9,102.8,102.5,M.DEFAULTS,progress=.1,spread=.01)=='swing_entry_insufficient_room'
    ctx=M.context([level(103,-1)],103,NOW.timestamp())
    assert M.entry(ctx,103,102.8,102.5,M.DEFAULTS,progress=.1,spread=.01)=='swing_entry_inside_resistance'
    assert M.entry({},103,104,102,M.DEFAULTS,progress=.1,spread=.01)=='swing_entry_not_green'


def test_progress_is_causal_and_bounded():
    state={}
    for t in range(100):M.update_progress(state,t,10+t*.01)
    assert len(state['swing_price_history'])<=5
    assert state['swing_price_progress']==pytest.approx(.03)
    M.update_progress(state,110,20)
    assert state['swing_price_progress'] is None


def test_rejection_requires_prior_approach_and_does_not_exit_on_breakout():
    settings=M.DEFAULTS;state={};res=dict(lower=10,upper=10.1,id='r',confirmed=1)
    for t,p in [(3,9.9),(4,10.05)]:
        _,reason=M.manage({'resistance':res},p,t,2,9,.01,state,settings)
        assert not reason
    _,reason=M.manage({},9.99,5,2,9,.01,state,settings)
    assert reason=='swing_resistance_rejected'
    state={}
    for t,p in [(3,9.9),(4,10.05),(5,10.2),(6,10.05)]:
        _,reason=M.manage({'resistance':res},p,t,2,9,.01,state,settings)
        assert not reason


def test_new_support_ratchets_without_loosening_or_rewriting_prior_state():
    state={};ctx={'support':dict(lower=10,upper=10.1,confirmed=4)}
    stop,_=M.manage(ctx,11,5,2,9,.01,state,M.DEFAULTS)
    assert stop==pytest.approx(9.99)
    assert M.manage({},10.5,6,2,stop,.01,state,M.DEFAULTS)[0]==stop


def test_real_engine_uses_policy_and_preserves_old_candidate():
    p=policy();p['swing_momentum_contract']=M.CONTRACT
    state={'last_red_entry_candle':dict(timestamp=NOW.timestamp()-2,open=103,close=102),
           'swing_price_history':[(NOW.timestamp()-3,102.5)]}
    market=replace(obs(),structural_support_levels=(level(102),))
    engine=S.LongMomentumStrategyEngine(revision=47)
    result=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=deepcopy(state)),market)
    assert entered(result),[s.reason for s in result.evaluation.signals]
    blocked=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=deepcopy(state)),replace(market,bar_open=104))
    assert not entered(blocked)
    at=NOW+timedelta(seconds=1)
    position=replace(market,observed_at=at,price=104,position_quantity=100,average_price=103.3,
        structural_support_levels=(level(103.5,stamp=at.timestamp()),))
    result.state['entry_at']=NOW.isoformat()
    held=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=result.state,status=S.AssignmentStatus.MANAGING),position)
    assert held.state['active_stop']==pytest.approx(103.48)
