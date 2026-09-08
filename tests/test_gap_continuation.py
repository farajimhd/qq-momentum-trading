from dataclasses import replace
from datetime import timedelta
import pytest
from src.trading_runtime import gap_continuation as C, swing_gap as G, strategy_engine as S
from tests.test_swing_gap import parameters, market
from tests.test_swing_momentum import level
from tests.test_long_momentum_strategy import assignment
from tests.test_swing_evidence_strategy import entered


def setup():
    p = parameters()
    p['swing_gap_contract'] = G.CONTRACT
    G.configure(p)
    def mature(price, side=1):
        return dict(level(price, side), structural_mature=True)
    m = replace(market(), price=3.54, bar_open=3.53, bid=3.53, ask=3.54,
        execution_vwap=3.4, structural_support_levels=(mature(3.42),),
        structural_resistance_levels=(mature(3.52, -1), mature(4, -1)))
    return p, m


def test_breakout_requires_clearance_and_executable_reward():
    p, m = setup()
    result = G.select(m, p)
    assert result['reason'] == ''
    assert result['target'] == pytest.approx(3.98)
    assert result['stop'] == pytest.approx(3.40)
    assert result['maximum_buy_price'] == pytest.approx(3.55)
    assert G.select(replace(m, price=3.52), p)['reason'] == 'gap_inside_resistance_cluster'
    assert G.select(replace(m, ask=3.9), p)['reason']
    immature = tuple(dict(r, structural_mature=False) for r in m.structural_resistance_levels)
    assert G.select(replace(m, structural_resistance_levels=immature), p)['reason']


def test_real_engine_emits_executable_price_cap():
    p, m = setup()
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=p), m)
    assert entered(result), [s.reason for s in result.evaluation.signals]
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.metadata['gap_entry_ceiling'] == pytest.approx(3.55)
    assert entry.resolved_execution_policy().envelope.maximum_buy_price == pytest.approx(3.55)


def test_new_role_cannot_inherit_old_roles_maturity():
    from src.trading_runtime.normalized_level_book import merge_levels
    row = dict(level(), prominence=3, independent_retests=10, best_departure=5, history_threshold=1,
               last_role_change_at_ms=123, role_retests=0)
    assert not merge_levels([row])[0]['structural_mature']
    row['role_retests'] = 1
    assert merge_levels([row])[0]['structural_mature']
    other = dict(row, unified_level_id='other', role_retests=0)
    assert not merge_levels([row, other])[0]['structural_mature']


def test_partial_target_keeps_runner_instead_of_liquidating():
    import asyncio
    from types import SimpleNamespace
    p, m = setup()
    assigned = assignment(strategy_revision=47, parameters=p, status=S.AssignmentStatus.MANAGING,
        state=dict(active_stop=3.4, initial_stop=3.4, entry_reference_price=3.54,
                   entry_at=m.observed_at.isoformat(), structural_profit_targets=[3.98]))
    strategy = S.AssignedLongMomentumStrategy([assigned])
    asyncio.run(strategy.on_order_group_update(SimpleNamespace(action='exit', assignment_id=assigned.assignment_id,
        fill_role='profit_target', fill_incremental_quantity=50, slice_id='target-slice-1',
        state='partially_filled', updated_at=m.observed_at), aggregate_position_quantity=50))
    assert not strategy.assignments()[0].state['profit_target_liquidation_required']


def test_wick_touch_needs_later_departure_and_failed_support_reclaim():
    p, m = setup()
    rows = G.levels(m, p['swing_gap'])
    support = rows[0]
    identity = C.key(support)
    state = {'gap_evidence': {'failed': {identity: m.observed_at.timestamp()-1}}}
    touch = replace(m, price=3.45, bar_low=3.42, bar_high=3.46,
                    evaluation_events=('bar_close',), source_timeframe='1s')
    C.observe(touch, p, state, rows)
    assert not state['gap_evidence']['touches'][identity]['departed']
    assert identity in state['gap_evidence']['failed']
    departure = replace(touch, observed_at=touch.observed_at+timedelta(seconds=1),
                        price=3.54, bar_low=3.5, bar_high=3.55)
    C.observe(departure, p, state, rows)
    assert state['gap_evidence']['touches'][identity]['departed']
    assert identity not in state['gap_evidence']['failed']
    snapshot = repr(state)
    C.observe(touch, p, state, rows)
    assert repr(state) == snapshot


def test_runner_protection_and_support_only_ratchet():
    p, m = setup()
    p['protection_profile_catalog'] = {'test': {'profile_id': 'test', 'slices': [
        {'slice_id': 'main', 'quantity_fraction': 1, 'stop': {'rule_type': 'fixed_price', 'price': 1}}]}}
    profile = S._protection_profile_from_phase({'protection_profile': 'test'}, observation=m,
        action='enter_long', quantity=100, parameters=p, state={'structural_profit_targets': [3.98]},
        invalidation_price=3.4, profit_target_price=3.98, trailing_amount=None)
    assert [s.quantity_fraction for s in profile.slices] == [.5, .5]
    assert profile.slices[0].profit_target_price == pytest.approx(3.98)
    assert profile.slices[1].profit_target_price is None
    state = dict(active_stop=3.4, entry_at=(m.observed_at-timedelta(seconds=10)).isoformat())
    row = dict(level(3.48), structural_mature=True)
    assert C.ratchet(m, p, state, [row]) == pytest.approx(3.46)
    state['active_stop'] = 3.5
    assert C.ratchet(m, p, state, [row]) == 3.5
