"""Independent two-threshold long policy; only shared execution DTOs are used."""
from copy import deepcopy
from datetime import datetime
from math import isfinite
from uuid import uuid4

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName
from .signals import StrategyEvaluation, StrategyIntent, StrategySignal

CONTRACT = 'macd-threshold-100ms-1'
DEFAULTS = dict(gap_bps=10., quantity=100., minimum_dollar_volume=1_000_000.,
                minimum_share_volume=100_000., minimum_trade_rate=5., maximum_spread_bps=100.,
                source_age_ms=2000., quote_age_ms=1000.)


def settings(parameters):
    raw=parameters.get('macd_threshold',{})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown MACD threshold parameter')
    result={**DEFAULTS,**raw}
    if any(type(v) not in (int,float) or not isfinite(v) or v<=0 for v in result.values()):
        raise ValueError('MACD threshold settings must be positive finite numbers')
    return result


def quality(observation, config):
    def source(key, age):
        record=observation.source_values.get(key)
        if not isinstance(record,dict):
            return None
        try:
            at=datetime.fromisoformat(str(record['observed_at']).replace('Z','+00:00'))
            value=float(record['value'])
            elapsed=(observation.observed_at-at).total_seconds()*1000
            return value if isfinite(value) and 0<=elapsed<=age else None
        except (KeyError,TypeError,ValueError):
            return None
    facts={name:source(key,config['source_age_ms']) for name,key in (
        ('dollar_volume','market.session_dollar_volume'),('share_volume','market.volume'),
        ('rate_10s','market.trade_rate_10s'),('rate_60s','market.trade_rate_60s'))}
    quote=source('market.spread_bps',config['quote_age_ms'])
    bid,ask=observation.bid,observation.ask
    valid=quote is not None and all(isfinite(x) for x in (bid,ask)) and 0<bid<=ask
    facts['spread_bps']=(ask-bid)/((ask+bid)/2)*10000 if valid else None
    limits=dict(dollar_volume=config['minimum_dollar_volume'],share_volume=config['minimum_share_volume'],
                rate_10s=config['minimum_trade_rate'],rate_60s=config['minimum_trade_rate'])
    checks={key:facts[key] is not None and facts[key]>=limit for key,limit in limits.items()}
    checks['spread']=valid and facts['spread_bps']<=config['maximum_spread_bps']
    return dict(facts=facts,checks=checks,failed=[k for k,v in checks.items() if not v])


def evaluate(assignment, observation):
    from .strategy_engine import AssignmentStatus as Status, StrategyEngineResult
    config=settings(assignment.parameters)
    state=deepcopy(assignment.state)
    now=observation.observed_at.timestamp()
    closed=('bar_close' in observation.evaluation_events and observation.source_timeframe=='100ms'
            and now>state.get('macd_threshold_at',0))
    valid=all(x is not None and isfinite(x) for x in (observation.macd_line,observation.macd_signal,observation.price)) and observation.price>0
    gap=None
    if closed:
        state['macd_threshold_at']=now
        if valid:
            gap=10000*(observation.macd_line-observation.macd_signal)/observation.price
    gates=quality(observation,config)
    metadata=dict(assignment_id=assignment.assignment_id,contract=CONTRACT,
        session_routing='smart',eligible_sessions=['premarket','regular','after_hours'],
        macd=dict(timeframe='100ms',gap_bps=gap,entry_above_bps=-config['gap_bps']/2,
                  exit_below_bps=-config['gap_bps']),liquidity_admission=gates,
        bid=observation.bid,ask=observation.ask,reference_price=observation.price)
    acquired=observation.position_quantity>0
    pending=assignment.status==Status.ENTRY_PENDING
    def emit(action,reason,status,quantity=0.):
        identity=str(uuid4())
        details={**metadata,'reason_code':reason,'status':status.value}
        signal=StrategySignal(identity,CONTRACT,observation.ticker,observation.observed_at,action,
            'bullish' if action=='enter_long' else 'bearish' if action=='exit' else 'neutral',
            1. if action=='enter_long' else 0.,1.,reason,observation.source_signal_ids,'100ms',metadata=details)
        intents=()
        if action in ('enter_long','exit','cancel_entry'):
            exiting=action=='exit'
            intent=StrategyIntent(identity,observation.ticker,observation.observed_at,action,quantity,
                observation.ask if action=='enter_long' else observation.price,
                execution_policy=ExecutionPolicy(policy_id='macd-threshold-execution',
                    name=ExecutionPolicyName.ADAPTIVE_URGENT,
                    envelope=ExecutionEnvelope(deadline_ms=100 if not exiting else 750,
                        persist_until_cancelled=exiting)),
                urgency='urgent',time_in_force='',outside_rth=False,reason=reason,
                metadata={**details,'reentry_after_fill':exiting,'cancel_entry_acquisition':exiting,
                          'position_fraction':1. if exiting else 0.})
            intents=(intent,)
        return StrategyEngineResult(StrategyEvaluation(signals=(signal,),intents=intents),state,status,signal.payload())
    if assignment.status==Status.EXIT_PENDING or observation.pending_exit_quantity>0:
        return emit('hold' if acquired else 'wait','exit_fill_pending',Status.EXIT_PENDING)
    if closed and gap is not None and gap < -config['gap_bps'] and (acquired or pending):
        return emit('exit','macd_below_exit_threshold',Status.EXIT_PENDING,observation.position_quantity)
    if pending and (gates['failed'] or now-state.get('entry_requested_at',0)>=.1
                    or (closed and (gap is None or gap<=-config['gap_bps']/2))):
        return emit('cancel_entry','acquisition_expired_or_invalid',Status.MANAGING if acquired else Status.WATCHING)
    if acquired:
        return emit('hold','position_held',Status.MANAGING)
    if pending:
        return emit('wait','entry_fill_pending',Status.ENTRY_PENDING)
    if assignment.status in (Status.DISABLED,Status.PAUSED,Status.COMPLETED,Status.ERROR) or not assignment.permissions.observe:
        return emit('wait','assignment_not_active',assignment.status)
    allowed=assignment.permissions.reenter if state.get('entered') else assignment.permissions.enter
    if not allowed:
        return emit('wait','entry_not_authorized',assignment.status)
    if not closed or gap is None:
        return emit('wait','waiting_for_completed_100ms_macd',assignment.status)
    if gap<=-config['gap_bps']/2:
        return emit('wait','macd_not_above_entry_threshold',assignment.status)
    if gates['failed']:
        return emit('wait','entry_gates_failed',assignment.status)
    # A completed liquidation must not latch the next acquisition into exit-pending.
    state.update(entered=True,entry_requested_at=now,entry_acquisition_exit_latched=False)
    return emit('enter_long','macd_above_entry_threshold',Status.ENTRY_PENDING,config['quantity'])
