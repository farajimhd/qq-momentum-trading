"""Opt-in, discovery-only MACD/pressure policy; older candidates stay unchanged."""
from copy import deepcopy

CONTRACT = "macd-open-selling-veto-v1"


def entry_rules():
    stage = {"operator": "all", "rule_sets": [{"rule_set_id": "evidence-macd-open-1s", "enabled": True,
        "operator": "all", "conditions": [{"condition_id": "line-above-signal",
        "left_source_id": "indicator.macd.line", "left_timeframe": "1s",
        "right_source_id": "indicator.macd.signal", "right_timeframe": "1s",
        "comparator": "greater_than"}]}]}
    veto = deepcopy(stage)
    veto["rule_sets"][0]["rule_set_id"] = "evidence-macd-closed"
    veto["rule_sets"][0]["conditions"][0]["comparator"] = "less_or_equal"
    return {"trigger": deepcopy(stage), "confirmation": deepcopy(stage), "veto": veto}


def apply(parameters):
    """Resolve the whole opt-in contract so inherited experiments cannot conflict."""
    if parameters.get("swing_evidence_contract") != CONTRACT:
        raise ValueError("Unknown swing evidence contract")
    if parameters.get("strategy_behavior", {}).get("side", "long") != "long":
        raise ValueError("Swing evidence candidate is long-only")
    parameters.update(local_swing_management=False, completed_macd_setup=True,
        require_completed_entry_candle=True, require_completed_macd_exit=True,
        require_breakout_reset=False, strict_green_entry=False,
        require_positive_macd_signal_for_entry=False, broken_level_stop_only=False)
    for key in ("macd_histogram_gate_bps", "macd_histogram_entry_gate_bps", "normalized_macd_threshold_bps"):
        parameters.pop(key, None)
    parameters["entry_candle_confirmation"].update(enabled=True, require_closed_bar=True,
        evaluate_macd_intrabar=False, reject_bearish_close=False,
        minimum_macd_open_gap_bps=0, minimum_reentry_macd_gap_bps=0,
        slope_reentry_break_previous_high=False)
    parameters["entry_momentum_confirmation"]["enabled"] = False
    parameters["structural_entry"]["enabled"] = False
    parameters["entry_rules"] = entry_rules()
    phase = parameters.setdefault("phase_policy", {})
    if phase.get("reentry"):
        phase["reentry"]["rules"] = entry_rules()
    phase.setdefault("exit", {})["rule_sets"] = []
    parameters["reentry"].update(require_new_confirmation=False, cooldown_ms=0)
    parameters["reentry"].setdefault("pullback_reclaim", {})["enabled"] = False
    parameters.setdefault("market_pressure", {}).update(enabled=True, minimum_trades=3,
        minimum_classified_fraction=.5, trade_imbalance=.3)
    stop = parameters["protection"]["stop"]
    stop.update(method="last_red_candle_close", structure_buffer_bps=0)
    parameters["protection"]["trailing"]["enabled"] = False
    parameters["protection"]["profit_ladder"].update(enabled=False, minimum_entry_target_gap_bps=0)
    parameters["protection"]["luld_profit_target"]["enabled"] = False
    parameters["profit_pocket"]["enabled"] = False
    management = parameters["momentum_management"]
    for key in ("histogram_slope_exit", "downside_loss_guard", "structure_failure", "qmd_exhaustion", "failure_to_extend"):
        management.setdefault(key, {})["enabled"] = False
    management["resistance_rejection_exit"] = False
    management["macd_backstop"].update(enabled=True, timeframe="1s", close_condition="signal_above_line")
    liquidity = parameters.setdefault("liquidity_admission", {})
    for key in ("minimum_vwap_extension_bps", "minimum_initial_vwap_extension_bps", "minimum_reentry_vwap_extension_bps"):
        liquidity[key] = 0
    liquidity.pop("maximum_vwap_extension_bps", None)


def selling_veto(pressure):
    return bool(pressure.get("usable") and pressure.get("fast", {}).get("trade_imbalance", 0) < -.3)
