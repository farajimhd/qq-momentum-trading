from dataclasses import replace
import pytest
from src.trading_runtime import swing_gap as G, strategy_engine as S
from tests.test_swing_evidence_strategy import policy, obs, entered
from tests.test_swing_momentum import level
from tests.test_long_momentum_strategy import assignment, NOW


def parameters():
    p = policy()
    p['swing_gap_contract'] = G.CONTRACT
    return S.resolve_long_momentum_parameters(p, revision=47)


def market():
    return replace(obs(), execution_vwap=102., bid=103.29, ask=103.31,
                   structural_support_levels=(level(103.25), level(103)),
                   structural_resistance_levels=(level(103.4, -1), level(104, -1)))


def test_support_not_red_close_and_minimum_distance():
    selected = G.select(market(), parameters())
    assert not selected['reason']
    assert selected['stop'] == pytest.approx(102.98)
    assert selected['target'] == pytest.approx(103.97)
    assert G.select(replace(market(), structural_support_levels=(level(103.25),)), parameters())['reason'] == 'gap_support_minimum_distance_unavailable'


def test_future_levels_and_vwap_block():
    m = replace(market(), structural_support_levels=(level(103, stamp=NOW.timestamp()+1),))
    assert 'unavailable' in G.select(m, parameters())['reason']
    assert G.select(replace(market(), execution_vwap=104), parameters())['reason'] == 'gap_price_not_above_vwap'


def test_real_engine_enters_with_fixed_target_and_holds_on_macd_close():
    p = parameters()
    engine = S.LongMomentumStrategyEngine(revision=47)
    result = engine.evaluate(assignment(strategy_revision=47, parameters=p), market())
    assert entered(result), [s.reason for s in result.evaluation.signals]
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.invalidation_price == pytest.approx(102.98)
    assert entry.profit_target_price == pytest.approx(103.97)
    m = replace(market(), position_quantity=100, average_price=103.3, macd_line=-.3, macd_signal=-.2)
    held = engine.evaluate(assignment(strategy_revision=47, parameters=p, state=result.state,
        status=S.AssignmentStatus.MANAGING), m)
    assert not any(i.action in ('exit_long', 'replace_profit_target', 'replace_protective_stop') for i in held.evaluation.intents)
    assert S._ratcheted_stop(m, p, result.state, side='long') == pytest.approx(102.98)


def test_pending_exit_prevents_reentry():
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=parameters()), replace(market(), pending_exit_quantity=1))
    assert not entered(result)


def test_attached_protection_is_one_fixed_stop_and_gap_target():
    p = parameters()
    p['protection_profile_catalog'] = {'test': {'profile_id': 'test', 'slices': [
        {'slice_id': 'main', 'quantity_fraction': .5, 'stop': {'rule_type': 'fixed_price', 'price': 1}},
        {'slice_id': 'runner', 'quantity_fraction': .5, 'stop': {'rule_type': 'fixed_price', 'price': 1}}]}}
    profile = S._protection_profile_from_phase({'protection_profile': 'test'}, observation=market(),
        action='enter_long', quantity=100, parameters=p, state={'structural_profit_targets': [103.97]},
        invalidation_price=102.98, profit_target_price=103.97, trailing_amount=None)
    assert len(profile.slices) == 1
    assert profile.slices[0].quantity_fraction == 1
    assert profile.slices[0].stop.price == 102.98
    assert profile.slices[0].profit_target_price == 103.97


def test_rebound_and_reward_room():
    m = replace(market(), execution_vwap=103.29,
        structural_resistance_levels=(level(104, -1),))
    selected = G.select(m, parameters())
    assert not selected['reason']
    assert selected['gap']['setup'] == 'support_vwap_rebound'
    assert G.select(replace(m, bar_open=104), parameters())['reason'] == 'gap_setup_unavailable'


def test_reentry_has_no_fresh_cross_or_cooldown_and_target_is_fixed():
    p = parameters()
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p, state={'entries': 1, 'reentries': 0,
            'last_exit_reason': 'profit_target'}, status=S.AssignmentStatus.WATCHING), market())
    assert entered(result), [s.reason for s in result.evaluation.signals]
    assert p['reentry']['cooldown_ms'] == 0
    assert not p['reentry']['require_new_confirmation']
    assert S.LongMomentumStrategyEngine(revision=47)._structural_target_replacement_result(
        assignment(parameters=p), market(), p, result.state, side='long', stop=102.98) is None
