"""Bounded, prefix-only context for hypothetical structural positions.

Thirty-second aggregates are published only once closed. The current candle
can supply fast evidence, but cannot rewrite the broader trend's history.
All strengths and hypotheses are deterministic rules, not probabilities.
"""
from copy import deepcopy


def observe(state, row):
    bar = row['candle']; at = bar['end']
    recent = state.setdefault('bars', [])
    recent[:] = [b for b in recent if at-b['end'] <= 180]
    recent.append(deepcopy(bar))
    buckets = {}
    for b in recent:
        bucket = int(b['time']//30)*30
        if b['end'] > bucket+30 or bucket+30 > at:
            continue
        g = buckets.setdefault(bucket, dict(start=bucket, end=bucket+30,
            open=b['open'], high=b['high'], low=b['low'], close=b['close'], count=0))
        g.update(high=max(g['high'], b['high']), low=min(g['low'], b['low']),
                 close=b['close'], count=g['count']+1)
    # Do not treat the left-truncated first bucket as a complete observation.
    completed = [g for g in buckets.values() if g['start'] >= recent[0]['time']]
    closes = [g['close'] for g in completed]
    travel = sum(abs(b-a) for a,b in zip(closes,closes[1:]))
    net = closes[-1]-closes[0] if len(closes)>1 else 0
    efficiency = abs(net)/travel if travel else 0
    direction = (1 if net>0 else -1) if len(closes)>=3 and efficiency>=.55 else 0
    fast = [b for b in recent if at-b['end']<=20]
    atr = row['qualification'].get('atr') or 0
    fast_net = fast[-1]['close']-fast[0]['open']
    fast_direction = (1 if fast_net>0 else -1) if abs(fast_net)>=atr and atr else 0
    regime = 'trend' if direction else 'rotation' if len(closes)>=3 else 'developing'
    old = state.get('direction',0)
    if direction and direction != old:
        state['leg_started_at'] = at
    state['direction'] = direction
    evidence = state.setdefault('events', [])
    evidence[:] = [e for e in evidence if at-e['at']<=60]
    for event in row['global_events']+row['local_events']:
        level = event['level']
        witness = (event['state'],level.get('lower'),level.get('upper'),event.get('break_at'),event.get('encounters'))
        if not any(tuple(e['witness'])==witness for e in evidence):
            evidence.append(dict(at=at,witness=list(witness),state=event['state'],level=deepcopy(level)))
    if len(evidence)>4096:
        raise ValueError('Structural thesis evidence capacity exceeded')
    return dict(regime=regime,direction=direction,fast_direction=fast_direction,
        global_bias=row.get('global_bias','unknown'),local_bias=row.get('local_bias','unknown'),
        efficiency=efficiency,completed_buckets=len(completed),
        context_through=completed[-1]['end'] if completed else None,
        leg_started_at=state.get('leg_started_at'),
        pullback_allowance=max(atr, sum(g['high']-g['low'] for g in completed[-3:])/max(1,len(completed[-3:]))*.5),
        evidence=deepcopy(evidence),fast_bars=fast)


def pressure(context, sign, trigger, atr):
    """Repeated tests with a rising/falling base, without a close through it."""
    bars = context['fast_bars']
    if len(bars)<6 or context['direction'] not in (0,sign) or context['fast_direction']!=sign:
        return False
    edge = trigger['lower'] if sign==1 else trigger['upper']
    if not 0 <= sign*(edge-bars[-1]['close']) <= atr:
        return False
    # A sequence of unresolved rejections is supply/demand defending the band,
    # not breakout pressure merely because several candles touched it.
    rejection_states=('rejection','failed_breakout') if sign==1 else ('support_rejection','failed_breakdown')
    relevant=[e for e in context.get('evidence',[]) if
        e['level'].get('lower',float('inf'))<=trigger['upper'] and
        e['level'].get('upper',float('-inf'))>=trigger['lower']]
    if sum(e['state'] in rejection_states for e in relevant[-3:])>=2:
        return False
    halves = (bars[:len(bars)//2],bars[len(bars)//2:])
    base = [min(b['low'] for b in h) if sign==1 else max(b['high'] for b in h) for h in halves]
    tests = [b for b in bars if abs((b['high'] if sign==1 else b['low'])-edge)<=atr*.5]
    volume = [b.get('volume') for b in bars]
    return (sign*(base[1]-base[0])>=atr*.2 and len(tests)>=2 and
            all(v is not None and v>0 for v in volume) and
            sum(volume[len(volume)//2:])/len(halves[1])>=sum(volume[:len(volume)//2])/len(halves[0])*.8)


def hypothesis(context, sign, origin, trigger, path, at):
    return dict(expected_next_move='pressure_then_breakout' if trigger else 'continuation_to_next_level',
        direction='long' if sign==1 else 'short',formed_at=at,
        regime=context['regime'],regime_direction=context['direction'],leg_started_at=context['leg_started_at'],
        protective_base=deepcopy(origin),trigger=deepcopy(trigger),path=deepcopy(path),
        evidence=deepcopy(context['evidence']),
        invalidation='protective_base_failure_or_important_opposing_rejection',
        confidence_basis='deterministic_conditions_not_calibrated_probability')
