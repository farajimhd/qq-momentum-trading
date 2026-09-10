"""Publish an immutable backtest-only 100ms MACD HOD candidate."""
from .structural_recovery_candidate import build as build_template
from src.trading_runtime.macd_hod import CONTRACT, DEFAULTS

PROFILE_ID = 'macd-hod-100ms-v1'
LABEL = '100ms MACD HOD breakout - hold episode'


def build(base):
    payload,canvas,plan=build_template(base,align_179=True,profile_id=PROFILE_ID,label_override=LABEL)
    profile=next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE_ID)
    p=profile['parameters']
    for key in ('structural_recovery_contract','structural_recovery','structural_detector_settings'):
        p.pop(key,None)
    p.update(macd_hod_contract=CONTRACT,macd_hod=dict(DEFAULTS))
    profile['description']='Completed 100ms MACD > signal, V6 R3/R2/HOD and VWAP entry; 181 tradability with 100bps spread; hold until stop, target, structural invalidation or bearish MACD gap >10bps. Same-episode high reentry. No ATR/range proximity filter.'
    # These are subscriptions, not additional entry gates. The policy consumes
    # the completed 100ms MACD and the independent completed 1s swing stream.
    rules=payload['market_discovery']['rule_sets']
    observe=next(r for r in rules if r['rule_set_id']==PROFILE_ID+'-observe')
    observe['conditions'][0]['left_timeframe']='100ms'
    observe['conditions'].extend([
        dict(condition_id='macd-observed',enabled=True,left_source_id='indicator.macd.line',left_timeframe='100ms',comparator='greater_than',value=-1000000.),
        dict(condition_id='signal-observed',enabled=True,left_source_id='indicator.macd.signal',left_timeframe='100ms',comparator='greater_than',value=-1000000.),
        dict(condition_id='vwap-observed',enabled=True,left_source_id='indicator.vwap.execution_value',left_timeframe='100ms',comparator='greater_than',value=0.),
        dict(condition_id='swing-observed',enabled=True,left_source_id='market.last_price',left_timeframe='1s',comparator='greater_than',value=0.),
    ])
    from copy import deepcopy
    for name in ('trigger','confirmation'):
        p['entry_rules'][name]={'operator':'all','rule_sets':[deepcopy(observe)]}
    for phase in ('initial_entry','reentry'):
        profile['lifecycle'][phase]['order_intent']['deadline_ms']=100
    return payload,canvas,plan


def create():
    from .trading_configuration_service import configuration_base,create_test_candidate
    payload,canvas,plan=build(configuration_base())
    return create_test_candidate(label=LABEL,canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=PROFILE_ID)
