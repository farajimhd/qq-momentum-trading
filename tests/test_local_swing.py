from dataclasses import replace
from datetime import timedelta
import json

from src.trading_runtime import local_swing as L, strategy_engine as S
from tests.test_fixed_support_trail import configured, market
from tests.test_long_momentum_strategy import NOW, assignment
from tests.test_market_pressure import evidence, PressureTracker, quote, trade


def observation(price=103.3, ms=0, high=103.27, low=103.):
    at=NOW+timedelta(milliseconds=ms)
    pressure=evidence(at)
    pressure['micro']=dict(high=high,low=low,trades=10,span_ms=600)
    return replace(market(price),observed_at=at,market_pressure=pressure,
                   evaluation_events=('market_data_update',),source_timeframe='')


def policy():
    p=configured()
    p.update(local_swing_management=True,macd_histogram_entry_gate_bps=.5,
             require_completed_entry_candle=False,completed_macd_setup=False)
    p['entry_candle_confirmation'].update(enabled=False,require_closed_bar=False)
    p['protection']['stop']['method']='local_swing_low'
    p['protection']['trailing']['enabled']=False
    p['protection']['profit_ladder'].update(enabled=False,minimum_entry_target_gap_bps=0)
    p['structural_entry']['accept_live_price_above_entry_level']=True
    return p


def test_micro_breakout_excludes_trigger_and_uses_prior_range():
    t=PressureTracker()
    t.observe(quote(0))
    for ms in (10,210,410):t.observe(trade(ms,100.))
    t.observe(trade(610,101.))
    snapshot=t.snapshot(NOW+timedelta(milliseconds=610))
    assert snapshot['micro']['high']==100.
    assert snapshot['fast']['progress_spreads']>0
    state={}
    assert L.entry(observation(),state,.01)['passed']
    initial_time=state['micro_breakout']['at']
    assert L.entry(observation(103.4,ms=100),state,.01)['passed']
    assert state['micro_breakout']['at']==initial_time
    state['previous_observed_price']=103.5
    assert not L.entry(observation(103.4,ms=200),state,.01)['passed']
    state.pop('previous_observed_price')
    assert not L.entry(observation(104,ms=900,high=103.9,low=103.),state,.01)['passed']
    assert not L.entry(replace(observation(),market_pressure={}),{},.01)['passed']


def test_swing_confirmation_does_not_move_stop_before_rebound_or_lower_it():
    state={'active_stop':99.,'entry_at':NOW.isoformat()}
    def step(price,ms):
        return L.update(replace(observation(price,ms,high=100,low=99.9),average_price=100,bid=price-.01,ask=price+.01),state,.01)
    step(100,0);step(101,100)
    pullback=step(100.8,200)
    assert pullback['phase']=='pullback'
    assert pullback['stop']==99.
    bounced=step(100.9,300)
    assert bounced['stop']==100.79
    assert bounced['low_confirmed_at']==(NOW+timedelta(milliseconds=300)).isoformat()
    checkpoint=json.loads(json.dumps(state))
    assert checkpoint['local_swing']['protected_low']==100.8
    step(101.2,400);step(100.7,500);step(100.8,600)
    assert state['local_swing']['stop']==100.79


def test_retest_needs_reversal_and_selling_evidence():
    state={'active_stop':99.,'entry_at':NOW.isoformat()}
    for ms,price in enumerate((100,101,100.7,100.8,100.98)):
        L.update(replace(observation(price,ms*100,high=100,low=99.9),average_price=100,bid=price-.01,ask=price+.01),state,.01)
    state['market_pressure']={'usable':True,'fast':{'trade_imbalance':-.8,'quote_imbalance':-.5}}
    result=L.update(replace(observation(100.9,600),average_price=100),state,.01)
    assert result['exit'] and result['reason']=='local_high_retest_failed'


def test_engine_enters_intrabar_with_small_macd_gap_and_has_no_fixed_target():
    engine=S.LongMomentumStrategyEngine(revision=47)
    obs=replace(observation(),macd_line=.2,macd_signal=.19,bar_open=104.)
    result=engine.evaluate(assignment(strategy_revision=47,parameters=policy()),obs)
    intent=next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert intent.invalidation_price==102.99
    assert intent.profit_target_price is None
    assert not result.state.get('structural_profit_targets')
    held=engine.evaluate(assignment(strategy_revision=47,parameters=policy(),state=result.state,status=S.AssignmentStatus.MANAGING),
        replace(observation(103.5,100),position_quantity=100,average_price=103.3,macd_line=-.1,macd_signal=.1))
    assert not any(i.action=='exit' for i in held.evaluation.intents)
    stopped=engine.evaluate(assignment(strategy_revision=47,parameters=policy(),state=held.state,status=S.AssignmentStatus.MANAGING),
        replace(observation(102.9,200),position_quantity=100,average_price=103.3))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in stopped.evaluation.intents)


def test_engine_publishes_higher_low_stop_and_reentry_requires_new_breakout():
    engine=S.LongMomentumStrategyEngine(revision=47)
    result=engine.evaluate(assignment(strategy_revision=47,parameters=policy()),observation())
    state=result.state
    for ms,price in ((100,103.4),(200,104.),(300,103.7),(400,103.85)):
        result=engine.evaluate(assignment(strategy_revision=47,parameters=policy(),state=state,status=S.AssignmentStatus.MANAGING),
            replace(observation(price,ms),position_quantity=100,average_price=103.3,bid=price-.01,ask=price+.01))
        state=result.state
    assert any(i.action=='replace_protective_stop' and i.reason=='local_higher_low_confirmed' for i in result.evaluation.intents)
    assert state['active_stop']==103.69
    micro_state={'last_exit_at':(NOW+timedelta(milliseconds=100)).isoformat()}
    assert not L.entry(observation(ms=0),micro_state,.01)['passed']
    assert L.entry(observation(ms=200),micro_state,.01)['passed']
