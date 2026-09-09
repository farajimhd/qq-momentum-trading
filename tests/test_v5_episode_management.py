from dataclasses import replace
from datetime import timedelta
import pytest
from src.trading_runtime import v5_macd_episode as M, strategy_engine as S
from tests.test_v5_macd_episode import rejection_setup


def scenario(closes=1):
    p, state, original = rejection_setup()
    p['episode_management'] = dict(rejection_from_below=True, rejection_closes=closes)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    level = M.overhead(state['v5_breakout_state']['levels'], original.price, p)[0]
    def observe(seconds, price, closed=False, held=100):
        o = replace(original, observed_at=original.observed_at+timedelta(seconds=seconds),
            price=price, position_quantity=held, source_timeframe='1s' if closed else '',
            evaluation_events=('bar_close',) if closed else ('market_data_update',))
        M.observe(o, p, state)
        return state['v5_breakout_state']
    return level, observe


def test_pullback_from_above_is_not_an_upward_rejection_attempt():
    r, observe = scenario()
    observe(1, r['upper']+.1)
    observe(1.2, r['price'])
    assert not observe(2, r['lower']-.1, True).get('resistance_rejection')


def test_rejection_requires_consecutive_completed_closes_and_resets_inside():
    r, observe = scenario(2)
    observe(1, r['lower']-.1)
    observe(1.2, r['price'])
    assert not observe(2, r['lower']-.1, True).get('resistance_rejection')
    assert not observe(2.1, r['lower']-.1).get('resistance_rejection')
    observe(3, r['price'], True)
    assert not observe(4, r['lower']-.1, True).get('resistance_rejection')
    rejected = observe(5, r['lower']-.1, True)['resistance_rejection']
    assert rejected['below_closes'] == 2
    assert rejected['approach_price'] < r['lower']


def test_confirmed_break_clears_attempt_and_flat_clears_lifecycle():
    r, observe = scenario(2)
    observe(1, r['lower']-.1)
    observe(1.2, r['price'])
    observe(2, r['upper']+.1, True)
    assert not observe(3, r['lower']-.1, True).get('resistance_rejection')
    observe(3.2, r['price'])
    observe(4, r['lower']-.1, True)
    assert observe(5, r['lower']-.1, True).get('resistance_rejection')
    assert not observe(5.1, r['lower']-.1, held=0).get('resistance_rejection')


@pytest.mark.parametrize('policy', [{'rejection_closes':0}, {'rejection_closes':1.5},
    {'rejection_from_below':1}, {'unknown':True}])
def test_invalid_policy_fails_closed(policy):
    p, _, _ = rejection_setup()
    p['episode_management'] = policy
    with pytest.raises(ValueError):
        S.resolve_long_momentum_parameters(p, revision=47)


def test_protected_runner_has_no_inherited_target():
    from tests.test_long_momentum_strategy import assignment
    p, state, o = rejection_setup()
    p['episode_management'] = dict(take_profit_fraction=.5)
    p['protection_profile_catalog'] = {'test': {'profile_id':'test', 'slices':[
        {'slice_id':'target', 'quantity_fraction':1., 'stop':{'rule_type':'fixed_price'}}]}}
    p['phase_policy']['initial_entry'] = dict(order_intent=dict(protection_profile='test',
        execution_policy='adaptive_urgent', partial_fill_policy='complete_remainder'))
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state=state), o)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    slices = entry.resolved_protection_profile().slices
    assert len(slices) == 2
    assert sum(s.quantity_fraction for s in slices) == 1
    assert slices[0].profit_target_price > o.price
    assert slices[1].profit_target_price is None
    assert not slices[1].inherit_profit_target
    assert slices[0].stop == slices[1].stop


def test_structural_stop_buffer_uses_closed_atr_and_never_lowers_stop():
    p, state, o = rejection_setup()
    p['episode_management'] = dict(stop_atr_multiple=2.)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    state.update(active_stop=98., initial_stop=98.)
    first = replace(o, observed_at=o.observed_at+timedelta(seconds=1), price=103.4,
        position_quantity=100, average_price=103.3, volatility=.1,
        source_timeframe='1s', evaluation_events=('bar_close',))
    M.observe(first,p,state)
    assert M.manage(first,p,state) == 98.
    closed = replace(first, observed_at=first.observed_at+timedelta(seconds=1),price=103.8)
    M.observe(closed,p,state)
    broken = state['v5_breakout_state']['crossed'][0]
    stop = M.manage(closed,p,state)
    assert 98 < stop <= broken['lower']-.2+1e-9
    state['active_stop'] = stop
    state['v5_breakout_state']['closed_atr'] = 10
    assert M.manage(closed,p,state) == stop


def test_close_entry_uses_prior_episode_bodies_and_rejects_intrabar_spikes():
    p, state, o = rejection_setup()
    p['episode_management'] = dict(entry_on_close=True)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    first = replace(o, observed_at=o.observed_at+timedelta(seconds=1), price=103.3, bar_open=103.3,
        source_timeframe='1s', evaluation_events=('bar_close',))
    M.observe(first,p,state)
    spike = replace(o, observed_at=o.observed_at+timedelta(seconds=1.1), price=103.5)
    M.observe(spike,p,state)
    assert M.select(spike,p,state)['reason'] == 'v5_waiting_for_entry_close'
    boundary = 103.3*(1+p['v5_breakout']['episode_high_offset_bps']/10000)
    equal = replace(first, observed_at=first.observed_at+timedelta(seconds=1), price=boundary)
    M.observe(equal,p,state)
    assert M.select(equal,p,state)['reason'] == 'v5_period_high_not_reclaimed'
    breakout = replace(first, observed_at=first.observed_at+timedelta(seconds=2), price=103.65,
        ask=103.66,bid=103.64)
    M.observe(breakout,p,state)
    assert state['v5_breakout_state']['prior_max'] == boundary
    assert M.select(breakout,p,state)['reason'] == ''
    assert state['v5_breakout_state']['period_max'] == 103.65


def test_recent_range_survives_macd_reset_and_excludes_current_bar():
    p, state, o = rejection_setup()
    p['episode_management'] = dict(entry_on_close=True, entry_range_seconds=30.)
    p = S.resolve_long_momentum_parameters(p, revision=47)
    first = replace(o, observed_at=o.observed_at+timedelta(seconds=1), price=103.3,
        bar_high=103.7,source_timeframe='1s',evaluation_events=('bar_close',))
    M.observe(first,p,state)
    reset = replace(first,observed_at=first.observed_at+timedelta(seconds=1),price=103.2,
                    bar_high=103.3,macd_line=0,macd_signal=1)
    M.observe(reset,p,state)
    rebound = replace(first,observed_at=first.observed_at+timedelta(seconds=2),price=103.5,
                      bar_high=103.5,ask=103.51,bid=103.49)
    M.observe(rebound,p,state)
    assert state['v5_breakout_state']['entry_range_high'] == 103.7
    assert M.select(rebound,p,state)['reason'] == 'v5_period_high_not_reclaimed'
    breakout = replace(rebound,observed_at=first.observed_at+timedelta(seconds=3),price=103.9,
                       bar_high=104.,ask=103.91,bid=103.89)
    M.observe(breakout,p,state)
    assert state['v5_breakout_state']['entry_range_high'] == 103.7
    assert M.select(breakout,p,state)['reason'] == ''


def test_profit_trail_uses_held_closes_and_ratchets_without_intrabar_peaks():
    from src.trading_runtime.v5_episode_management import profit_trail
    p, state, o = rejection_setup()
    policy = dict(profit_trail_atr_multiple=2., profit_trail_activation_atr=1.)
    state['v5_entry_selection'] = dict(entry_atr=.2)
    d = dict(closed_atr=.2)
    held = replace(o, position_quantity=100, average_price=100., price=100.2,
        source_timeframe='1s', evaluation_events=('bar_close',))
    assert profit_trail(held,d,state,policy,.01,98.) == 98.
    spike = replace(held, price=110., observed_at=held.observed_at+timedelta(seconds=.1),
        source_timeframe='',evaluation_events=('market_data_update',))
    assert profit_trail(spike,d,state,policy,.01,98.) == 98.
    closed = replace(held,price=101.,observed_at=held.observed_at+timedelta(seconds=1))
    assert profit_trail(closed,d,state,policy,.01,98.) == pytest.approx(100.6)
    assert d['profit_trail']['peak_close'] == 101.
    d['closed_atr'] = 1.
    pullback = replace(closed,price=100.8,observed_at=closed.observed_at+timedelta(seconds=1))
    assert profit_trail(pullback,d,state,policy,.01,98.) == pytest.approx(100.6)
    # A duplicate close cannot consume changed operands.
    assert profit_trail(replace(pullback,price=105.),d,state,policy,.01,98.) == pytest.approx(100.6)


def test_profit_trail_does_not_use_missing_entry_atr_and_clears_when_flat():
    from src.trading_runtime.v5_episode_management import profit_trail
    p, state, o = rejection_setup()
    policy = dict(profit_trail_atr_multiple=2., profit_trail_activation_atr=1.)
    d = dict(closed_atr=.2)
    held = replace(o,position_quantity=100,average_price=100.,price=105.,
        source_timeframe='1s',evaluation_events=('bar_close',))
    assert profit_trail(held,d,state,policy,.01,98.) == 98.
    state['v5_breakout_state']['profit_trail'] = dict(peak_close=200.)
    M.observe(replace(o,position_quantity=0,observed_at=o.observed_at+timedelta(seconds=1)),p,state)
    assert 'profit_trail' not in state['v5_breakout_state']


def test_intrabar_range_includes_latest_completed_high_and_freezes_between_closes():
    from src.trading_runtime.v5_episode_management import observe_entry
    p, _, o = rejection_setup()
    p['episode_management'] = dict(entry_on_close=False,entry_range_seconds=30.)
    p = S.resolve_long_momentum_parameters(p,revision=47)
    policy = p['episode_management']
    d = {}
    closed = replace(o,price=103.,bar_high=104.,source_timeframe='1s',evaluation_events=('bar_close',))
    observe_entry(closed,d,True,policy)
    assert d['entry_range_high'] == 104.
    d['closed_at'] = closed.observed_at.timestamp()
    spike = replace(closed,price=110.,bar_high=110.,observed_at=closed.observed_at+timedelta(seconds=.1))
    observe_entry(spike,d,False,policy)
    assert d['entry_range_high'] == 104.
    expired = replace(closed,price=102.,bar_high=102.,observed_at=closed.observed_at+timedelta(seconds=31))
    observe_entry(expired,d,True,policy)
    assert d['entry_range_high'] == 102.
    assert d['entry_range_samples'] == 1
