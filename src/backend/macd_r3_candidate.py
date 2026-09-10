"""Candidate declaration for 100ms MACD/VWAP with moving R3 protection."""
from .macd_threshold_candidate import build as build_declarations
from src.trading_runtime.macd_r3 import CONTRACT
from src.trading_runtime.macd_threshold import DEFAULTS

PROFILE_ID = 'macd-r3-100ms'
LABEL = '100ms MACD + VWAP + R3 / moving stop and R1 target'


def build(base):
    return build_declarations(base, profile_id=PROFILE_ID, label=LABEL,
        parameters=dict(macd_r3_contract=CONTRACT, macd_r3=dict(DEFAULTS)))


def create():
    from .trading_configuration_service import configuration_base, create_test_candidate
    payload, canvas, plan = build(configuration_base())
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=PROFILE_ID)
