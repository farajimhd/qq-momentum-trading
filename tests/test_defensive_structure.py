from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import pytest
from src.trading_runtime import expansion_stop as E, v5_macd_episode as M, strategy_engine as S
from tests.test_expansion_stop import fixture
from tests.test_long_momentum_strategy import assignment


def setup():
    p, state, o, levels = fixture()
    p['episode_management']['defensive_structure_enabled'] = True
    p = S.resolve_long_momentum_parameters(p, revision=47)
    return p, state, o, levels


def test_preentry_break_survives_flat_and_later_progress_without_recross():
    p, state, o, levels = setup()
    d = state['v5_breakout_state']
    E.observe_break(replace(o, position_quantity=0), d)
    assert d['confirmed_stop_break']['resistance']['upper'] == levels[1]['upper']
    state = json.loads(json.dumps(state))
    state['v5_breakout_state']['crossed'] = []
    later = replace(o, observed_at=o.observed_at+timedelta(seconds=1), price=103.1, bid=102., ask=104.)
    assert E.manage(later, p, state, 99.) == pytest.approx(101.98)


def test_spread_does_not_shift_stop_and_exhaustion_needs_no_extra_progress():
    p, state, o, levels = setup()
    state['v5_breakout_state']['adaptive_target'] = {'paused':True}
    for bid, ask in ((102.19,102.21),(101.5,103.)):
        s = deepcopy(state)
        assert E.manage(replace(o,bid=bid,ask=ask),p,s,99.) == pytest.approx(101.98)
        assert s['trailing_support_selection']['exhaustion']


def test_breached_defensive_boundary_requests_exit_not_invalid_stop():
    p, state, o, levels = setup()
    d=state['v5_breakout_state']
    d.update(crossed=[], expansion_stop={'resistance':levels[1], 'next_lower':104., 'broken_at':1},
             adaptive_target={'paused':True})
    assert E.manage(replace(o,price=101.),p,state,99.) == 99.
    assert d['defensive_structure_failure']['reason']=='defensive_stop_already_breached'


def test_engine_exits_on_detected_forming_resistance_without_support_failure():
    p,state,o,levels=setup()
    state.update(active_stop=99.,initial_stop=99.,entry_reference_price=100.)
    # Saved causal structure evidence must produce an exit on the next frame,
    # without inventing a lower support failure or waiting for more candles.
    d=state['v5_breakout_state']
    d.update(position_structure={'resistance':{'lower':103.,'upper':104.,'source':'internal_swing',
        'confirmed_at':o.observed_at.timestamp()}},fill_stop_initialized=True)
    o=replace(o,observed_at=o.observed_at+timedelta(seconds=1),evaluation_events=('market_data_update',),
              source_timeframe='',average_price=100.)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47,parameters=p,state=state),o)
    exits=[i for i in result.evaluation.intents if i.action=='exit']
    assert len(exits)==1 and exits[0].quantity==o.position_quantity
    assert exits[0].reason=='forming_resistance'
