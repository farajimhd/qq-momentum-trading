from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import strategy_engine as S
from src.trading_runtime.swing_evidence import CONTRACT
from tests.test_fixed_support_trail import configured, market
from tests.test_market_pressure import evidence
from tests.test_long_momentum_strategy import assignment, NOW


def policy():
    p = configured()
    p.update(swing_evidence_contract=CONTRACT)
    return p


def obs(**changes):
    return replace(market(), macd_line=-.1, macd_signal=-.2, execution_vwap=200,
                   structural_resistance_levels=(), structural_support_levels=(),
                   **changes)


def evaluate(observation=None, state=None, status=S.AssignmentStatus.WATCHING):
    state = state if state is not None else {"last_red_entry_candle": {
        "timestamp": NOW.timestamp()-2, "open": 103, "close": 102}}
    return S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=policy(), state=state, status=status),
        observation or obs())


def entered(result):
    return any(i.action == "enter_long" for i in result.evaluation.intents)


def test_negative_macd_below_vwap_no_structure_and_missing_pressure_enter():
    result = evaluate()
    assert entered(result), [s.reason for s in result.evaluation.signals]
    entry = next(i for i in result.evaluation.intents if i.action == "enter_long")
    assert entry.invalidation_price == 101.99
    assert not result.state["structural_profit_targets"]


@pytest.mark.parametrize("imbalance,blocked", [(-.31, True), (-.3, False), (.8, False)])
def test_selling_veto_exact_boundary_and_no_quote_flow_conjunction(imbalance, blocked):
    p = evidence()
    p["fast"].update(trade_imbalance=imbalance, quote_imbalance=.4)
    result = evaluate(obs(market_pressure=p))
    assert entered(result) is not blocked
    if blocked:
        assert result.evaluation.signals[0].reason == "entry_selling_pressure_veto"


@pytest.mark.parametrize("age", [-1, 1])
def test_unavailable_or_future_pressure_is_neutral(age):
    p = evidence(NOW+timedelta(seconds=age), True)
    assert entered(evaluate(obs(market_pressure=p)))


def test_forming_macd_does_not_authorize_entry_and_stop_is_required():
    assert not entered(evaluate(obs(evaluation_events=("market_data_update",), source_timeframe="")))
    result = evaluate(state={})
    assert not entered(result)
    assert result.evaluation.signals[0].reason == "last_red_candle_stop_unavailable"


def test_just_completed_red_candle_can_supply_stop_without_future_data():
    result = evaluate(obs(bar_open=104, source_timeframe="1s"), state={})
    assert entered(result)
    assert result.state["initial_stop"] == 103.29


def test_reentry_uses_same_veto_and_keeps_pending_capital_revalidation():
    state = {"reentries": 1, "pressure_exit_latched": True, "last_red_entry_candle": {
        "timestamp": NOW.timestamp()-2, "open": 103, "close": 102}}
    assert entered(evaluate(state=state))
    state["pending_capital_request"] = {"request_id": "test", "requested_at": NOW.isoformat()}
    closed = replace(obs(), macd_line=-.3, macd_signal=-.2)
    result = evaluate(closed, state)
    assert not entered(result)
    assert "pending_capital_request" not in result.state


def test_no_pressure_or_histogram_decline_exit_but_completed_close_exits():
    initial = evaluate()
    at = NOW+timedelta(seconds=1)
    position = replace(obs(), observed_at=at, position_quantity=100, average_price=103.3,
                       macd_line=-.15, macd_signal=-.2, market_pressure=evidence(at, True))
    held = evaluate(position, initial.state, S.AssignmentStatus.MANAGING)
    assert not any(i.action == "exit" for i in held.evaluation.intents)
    closed = evaluate(replace(position, macd_line=-.2, macd_signal=-.2), held.state, S.AssignmentStatus.MANAGING)
    assert any(i.action == "exit" and i.reason == "completed_macd_closed" for i in closed.evaluation.intents)


def test_stop_and_pending_exit_still_win():
    initial = evaluate()
    stopped = evaluate(replace(obs(), price=101, position_quantity=100, average_price=103.3),
                       initial.state, S.AssignmentStatus.MANAGING)
    assert any(i.action == "exit" and i.reason == "protective_stop" for i in stopped.evaluation.intents)
    assert not entered(evaluate(obs(pending_exit_quantity=10)))


def test_old_configuration_is_unchanged_and_contract_rejects_unknown():
    p = S.resolve_long_momentum_parameters(configured(), revision=47)
    assert p["structural_entry"]["enabled"] and p["macd_histogram_gate_bps"] == 5
    with pytest.raises(ValueError, match="Unknown swing"):
        S.resolve_long_momentum_parameters({"swing_evidence_contract": "unknown"}, revision=47)
