from dataclasses import replace
from datetime import timedelta
import json
import pytest
from src.trading_runtime import strategy_engine as S, v5_macd_episode as M
from tests.test_episode_reentry_stop import setup
from tests.test_long_momentum_strategy import assignment


def fixture():
    p,state,o=setup()
    p['macd_evaluation_mode']='completed_1s'
    p['episode_management'].update(completed_body_reentry_enabled=True, entry_on_close=True,
        entry_confirmation_window_ms=1000.,entry_minimum_close_location=.75,entry_range_seconds=10.)
    p=S.resolve_long_momentum_parameters(p,revision=47)
    episode=state['v5_breakout_state']['episode_started_at']
    state['last_acquired_macd_episode']=episode
    close=replace(o,observed_at=o.observed_at+timedelta(seconds=1),price=103.5,bar_open=103.6,
        bar_high=105.,bar_low=103.4,source_timeframe='1s',evaluation_events=('bar_close',))
    M.observe(close,p,state)
    # Prior completed body, weak close and higher wick/range context.
    state['v5_breakout_state'].update(period_max=103.6,entry_range_high=105.)
    return p,state,close


def test_intrabar_break_ignores_current_high_and_stale_confirmation_and_bid():
    p,state,close=fixture()
    frame=replace(close,observed_at=close.observed_at+timedelta(milliseconds=300),price=103.85,
        bid=103.4,ask=103.86,source_timeframe='',evaluation_events=('market_data_update',))
    # A larger intrabar price does not change the completed-body anchor.
    M.observe(replace(frame,observed_at=frame.observed_at-timedelta(milliseconds=100),price=104.),p,state)
    M.observe(frame,p,state)
    state=json.loads(json.dumps(state))
    chosen=M.select(frame,p,state)
    assert chosen['reason']==''
    assert chosen['stop_selection']['high']==103.6
    assert chosen['stop']==103.54
    assert state['v5_breakout_state']['entry_high_threshold']==pytest.approx(103.6*1.0015)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47,parameters=p,state=state),frame)
    entry=next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert entry.invalidation_price==chosen['stop']
    assert M.select(replace(frame,price=103.6*1.0015),p,state)['reason']=='v5_period_high_not_reclaimed'


def test_completed_candle_updates_next_anchor_and_target_fill_still_waits():
    p,state,close=fixture()
    state['last_profit_target_fill']={'filled_at':(close.observed_at+timedelta(milliseconds=100)).isoformat()}
    frame=replace(close,observed_at=close.observed_at+timedelta(milliseconds=200),price=103.85,
        source_timeframe='',evaluation_events=('market_data_update',))
    M.observe(frame,p,state)
    assert M.select(frame,p,state)['reason']=='v5_waiting_for_post_target_close'
    next_close=replace(close,observed_at=close.observed_at+timedelta(seconds=1),bar_open=103.8,price=103.9)
    M.observe(next_close,p,state)
    assert state['v5_breakout_state']['period_max']==103.9
    later=replace(frame,observed_at=next_close.observed_at+timedelta(milliseconds=100),price=103.95)
    M.observe(later,p,state)
    assert M.select(later,p,state)['reason']=='v5_period_high_not_reclaimed'
