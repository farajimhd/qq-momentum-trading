from dataclasses import replace
from datetime import timedelta
import json
import pytest
from src.trading_runtime import v5_breakout as V, strategy_engine as S
from src.trading_runtime import v5_macd_gap as M
from tests.test_v5_breakout import parameters, market, resistance
from tests.test_long_momentum_strategy import assignment, NOW


def setup():
    p = parameters()
    p['v5_breakout_contract'] = M.CONTRACT
    p = S.resolve_long_momentum_parameters(p, revision=47)
    state = {}
    o = market()
    V.observe(o, p, state)
    o = replace(o, observed_at=NOW+timedelta(milliseconds=1))
    V.observe(o, p, state)
    return p, state, o


def test_entry_has_no_body_rank_or_green_gate():
    p, state, o = setup()
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), replace(o, bar_open=110))
    assert any(i.action == 'enter_long' for i in result.evaluation.intents)
    assert result.state['initial_stop'] == pytest.approx(98.13)
    json.dumps(result.state)


def test_target_skips_small_intermediate_gaps():
    p, _, _ = setup()
    rows = [dict(resistance(x), lower=x-.01, upper=x+.01) for x in (100,102,102.5,103,104,105)]
    assert M.choose_target(rows,102.1,p)['level']['price'] == 104


def test_reentry_uses_period_max_and_resets_on_macd_close():
    p, state, o = setup()
    for offset, price, qty in ((1,103.8,100),(2,103.4,0)):
        V.observe(replace(o, observed_at=NOW+timedelta(seconds=offset), price=price,
            position_quantity=qty, source_timeframe='1s', evaluation_events=('bar_close',)),p,state)
    assert V.select(replace(o,price=103.5),p,state)['reason'] == 'v5_period_high_not_reclaimed'
    assert V.select(replace(o,price=103.9),p,state)['reason'] == ''
    V.observe(replace(o,observed_at=NOW+timedelta(seconds=3),macd_line=-1,macd_signal=0),p,state)
    V.observe(replace(o,observed_at=NOW+timedelta(seconds=4)),p,state)
    assert V.select(o,p,state)['reason'] == ''


def test_only_completed_lower_bound_cross_raises_stop():
    p, state, o = setup()
    state.update(active_stop=98, initial_stop=98)
    for offset, price, closed in ((1,103.4,True),(1.5,103.6,False)):
        m=replace(o,observed_at=NOW+timedelta(seconds=offset),price=price,position_quantity=100,
                  average_price=103.3,source_timeframe='1s' if closed else '',
                  evaluation_events=('bar_close',) if closed else ('market_data_update',))
        V.observe(m,p,state); state['active_stop']=V.manage(m,p,state)
    initial=state['active_stop']
    m=replace(m,observed_at=NOW+timedelta(seconds=2),source_timeframe='1s',evaluation_events=('bar_close',))
    V.observe(m,p,state)
    assert V.manage(m,p,state)>initial


def test_stall_is_provisional_and_not_backdated():
    p, state, o = setup()
    for second, high, close in ((1,102,101.9),(2,104,103.99),(3,104,103.99),(4,104,103.99)):
        m=replace(o,observed_at=NOW+timedelta(seconds=second),price=close,bar_high=high,
            bar_low=close-.01,source_timeframe='1s',evaluation_events=('bar_close',))
        V.observe(m,p,state)
        if second<4: assert not state['v5_breakout_state']['forming']
    assert state['v5_breakout_state']['forming']['available_at']==m.observed_at.timestamp()
    json.dumps(state)


def test_engine_exits_on_provisional_forming_resistance():
    p, state, o = setup()
    engine = S.LongMomentumStrategyEngine(revision=47)
    entered = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=state), o)
    state = entered.state
    for second, high, close in ((1,102,101.9),(2,104,103.99),(3,104,103.99),(4,104,103.99)):
        m = replace(o, observed_at=NOW+timedelta(seconds=second), price=close,
            bid=close-.01, ask=close+.01, bar_high=high, bar_low=close-.01,
            position_quantity=100, average_price=103.3,
            source_timeframe='1s', evaluation_events=('bar_close',))
        result = engine.evaluate(assignment(strategy_revision=47, parameters=p,
            state=state, status=S.AssignmentStatus.MANAGING), m)
        state = result.state
    assert any(i.action == 'exit' and i.reason == 'forming_resistance'
               for i in result.evaluation.intents)
