"""Bounded causal continuation/rejection state; no broker or hindsight inputs."""
from math import isfinite

from .adaptive_episode_target import body_mean


def observe(o, d, policy):
    now = o.observed_at.timestamp()
    s = d.setdefault('continuation_detector', {})
    if now < s.get('observed_at', 0):
        return
    holding = o.position_quantity > 0
    episode = d.get('episode_started_at')
    # An acquired position can span MACD episodes. Keep its structure intact.
    if not s or (not holding and s.get('episode') != episode):
        seq = s.get('sequence', 0)
        s.clear()
        s.update(episode=episode, sequence=seq, bodies=[], high=o.price)
    if holding and not s.get('holding'):
        s.pop('rejected', None)
    s.update(observed_at=now, holding=holding)
    closed = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    reason = s.get('reason', 'tracking_advance')
    state = s.get('state', 'advance' if d.get('macd_open') or holding else 'inactive')
    pb = s.get('pullback')
    if closed and now > s.get('closed_at', 0):
        values = (o.bar_open, o.bar_high, o.bar_low, o.price)
        if any(v is None or not isfinite(v) or v <= 0 for v in values):
            return
        if not o.bar_low <= min(o.bar_open, o.price) <= max(o.bar_open, o.price) <= o.bar_high:
            return
        previous = s.get('close')
        if s.get('closed_at') and now-s['closed_at'] != 1:
            # Missing bars cannot establish a recovery or its failure.
            s.pop('pullback', None)
            s.pop('candidate', None)
            s['bodies'] = []
            pb, previous = None, None
            state, reason = 'unresolved', 'candle_gap'
        baseline = body_mean(s['bodies'], policy)
        if previous is not None and o.price < previous and not pb:
            pb = dict(high=s['high'], low=o.bar_low, started_at=now, recovery=False)
            s['pullback'] = pb
            state, reason = 'pullback', 'retreat_support_unbroken'
        if pb:
            # Freeze the low BEFORE the recovery. A new low cannot lower its
            # own invalidation boundary on the failure candle.
            if pb['recovery'] and o.price < pb['floor']:
                s['rejected'] = 'failed_recovery_support_break'
            elif not pb['recovery']:
                pb['low'] = min(pb['low'], o.bar_low)
                if previous is not None and o.price > previous and o.price-pb['low'] > baseline*policy['detector_recovery_body_multiple']:
                    pb.update(recovery=True, floor=pb['low'], recovery_at=now)
                    state, reason = 'recovery', 'support_held_recovery_attempt'
        candidate = (d.get('position_structure') or {}).get('resistance')
        if candidate:
            s['candidate'] = {k: candidate[k] for k in ('lower', 'upper', 'confirmed_at', 'source') if k in candidate}
            support = candidate.get('support')
            if support:
                s.update(support_boundary=support['boundary'], support_at=now,
                         support_reason='resistance_support_break')
            if support and o.price < support['boundary']:
                s['rejected'] = 'resistance_support_break'
        established = (d.get('position_structure') or {}).get('established_support')
        if established and established['boundary'] > s.get('support_boundary', 0.):
            s.update(support_boundary=established['boundary'], support_at=now,
                     support_reason='established_support_break')
        if established and o.price < established['boundary']:
            s['rejected'] = 'established_support_break'
        s.update(closed_at=now, close=o.price,
                 bodies=[*s['bodies'], abs(o.price-o.bar_open)][-policy['adaptive_target_body_window']:])
        s['body_mean'] = body_mean(s['bodies'], policy)
        if o.price > s['high']:
            s['high'] = max(o.bar_open, o.price)
            s.pop('pullback', None)
            s.pop('candidate', None)
            pb = None
            if not s.get('rejected'):
                state, reason = 'advance', 'completed_high_reclaimed'
    # Recovery floor is established using completed candles; act on its first
    # subsequent observed breach, without inventing another confirmation wait.
    if pb and pb.get('recovery') and now > pb['recovery_at'] and o.price < pb['floor']:
        s['rejected'] = 'failed_recovery_support_break'
    if s.get('support_boundary') and now >= s['support_at'] and o.price < s['support_boundary']:
        s['rejected'] = s['support_reason']
    threshold = d.get('entry_high_threshold', 0.)
    if not holding and d.get('macd_open') and threshold > 0 and o.price > threshold:
        s.pop('rejected', None)
        s.pop('pullback', None)
        state, reason = 'continuation', 'episode_body_high_reclaimed'
    elif s.get('rejected'):
        state, reason = 'rejection', s['rejected']
    elif not holding and not d.get('macd_open'):
        state, reason = 'inactive', 'macd_episode_closed'
    elif not holding and state == 'continuation':
        state, reason = 'pullback', 'episode_body_high_not_reclaimed'
    action = 'exit' if holding and state == 'rejection' else 'hold' if holding else 'entry_eligible' if state == 'continuation' else 'wait'
    signature = [state, reason, action]
    if signature != s.get('signature'):
        s['sequence'] += 1
        s.update(signature=signature, state=state, reason=reason, since=o.observed_at.isoformat())
        s['decision'] = dict(contract='continuation-detector-1', sequence=s['sequence'],
            state=state, reason=reason, action=action, effective_at=s['since'],
            reference_price=o.price, advance_high=s['high'],
            pullback_low=(s.get('pullback') or {}).get('low'),
            recovery_floor=(s.get('pullback') or {}).get('floor'),
            support_boundary=s.get('support_boundary'),
            resistance=dict(s.get('candidate') or {}), body_mean=s.get('body_mean'),
            entry_threshold=threshold, episode=episode)
