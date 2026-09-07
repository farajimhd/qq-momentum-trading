from dataclasses import replace
from datetime import timedelta
import pytest
from src.trading_runtime import strategy_engine as S, breakout_confirmation as B
from tests.test_fixed_support_trail import configured, market
from tests.test_long_momentum_strategy import NOW, assignment


def policy():
    p=configured()
    p['structural_entry']['entry_level_ordinal_below_high']=3
    p['protection']['stop']['method']='last_red_candle_close'
    p['protection']['trailing'].update(mode='support_distance',enabled=False)
    return S.resolve_long_momentum_parameters(p,revision=47)


def test_engine_uses_last_red_close_not_low_and_keeps_stop_fixed():
    p=policy(); engine=S.LongMomentumStrategyEngine(revision=47)
    red=replace(market(100.8),observed_at=NOW-timedelta(seconds=1),bar_open=101.)
    recorded=engine.evaluate(assignment(strategy_revision=47,parameters=p),red)
    assert recorded.state['last_red_entry_candle']['close']==100.8
    entry=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=recorded.state),market(101.3))
    intent=next(i for i in entry.evaluation.intents if i.action=='enter_long')
    assert intent.invalidation_price==100.79
    assert intent.metadata['protective_stop_selection']['candle']['timestamp']==red.observed_at.timestamp()
    later=replace(market(101.8),observed_at=NOW+timedelta(seconds=1),position_quantity=100,average_price=101.3)
    managed=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=entry.state,status=S.AssignmentStatus.MANAGING),later)
    assert managed.state['active_stop']==100.79
    assert not any(i.action=='replace_protective_stop' for i in managed.evaluation.intents)


def test_forming_green_and_doji_do_not_replace_last_completed_red():
    state={}; red=replace(market(100.8),bar_open=101.,observed_at=NOW-timedelta(seconds=3))
    B.record_candle(state,red)
    for obs in [replace(red,observed_at=NOW-timedelta(seconds=2),evaluation_events=('market_data_update',)),
                replace(market(101),observed_at=NOW-timedelta(seconds=1),bar_open=101),market(101.3)]:
        B.record_candle(state,obs)
    assert state['last_red_entry_candle']['timestamp']==red.observed_at.timestamp()


@pytest.mark.parametrize('change',['missing','future','previous_session','above_entry'])
def test_missing_or_noncausal_red_candle_fails_closed(change):
    candle=dict(close=100.8,open=101.,timestamp=(NOW-timedelta(seconds=1)).timestamp())
    if change=='future':candle['timestamp']=(NOW+timedelta(seconds=1)).timestamp()
    if change=='previous_session':candle['timestamp']=(NOW-timedelta(days=1)).timestamp()
    if change=='above_entry':candle.update(close=110,open=111)
    state={} if change=='missing' else {'last_red_entry_candle':candle}
    assert S._initial_stop(market(101.3),policy(),None,side='long',candle_state=state)==0
