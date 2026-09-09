"""One immutable classification per completed 1s candle, independent of trades.

MACD episode changes are consumed at the candle boundary. Position quantity,
entry/exit callbacks and intrabar ticks cannot reset or rewrite market state.
"""
from datetime import timedelta
from math import isfinite

from .adaptive_episode_target import body_mean
from .position_structure import observe as observe_structure


def observe_stream(o, parameters, streams):
    """Passive ticker state: no assignment, position or entry authority required."""
    from .v5_macd_episode import gap_bps
    from .v5_breakout import rows
    if o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events:
        return None
    d = streams.setdefault(o.ticker, {})
    if o.observed_at.timestamp() <= (d.get('continuation_detector') or {}).get('closed_at', 0):
        return None
    gap = gap_bps(o)
    opened = gap is not None and gap >= parameters['v5_breakout']['minimum_macd_gap_bps']-1e-9
    if not opened:
        d.pop('episode_started_at', None)
    elif d.get('episode_started_at') is None:
        d['episode_started_at'] = o.observed_at.timestamp()
    d.update(macd_open=opened, closed_atr=max(0., o.volatility), decision_levels=rows(o, parameters))
    observe(o, d, parameters['episode_management'])
    detector = d['continuation_detector']
    detector['decision']['detector_id'] = 'episode-candles:' + o.ticker
    return detector


def observe(o, d, policy):
    if o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events:
        return
    now = o.observed_at.timestamp()
    old = d.get('continuation_detector') or {}
    if now <= old.get('closed_at', 0):
        return
    episode = d.get('episode_started_at') if d.get('macd_open') else None
    new_episode = old.get('contract') != 'candle-state-detector-1' or old.get('episode') != episode
    s = (dict(contract='candle-state-detector-1', episode=episode, bodies=[],
              sequence=old.get('sequence', 0), high=0., structure={}) if new_episode else old)
    previous = s.get('close')
    prior_high = s.get('high', 0.)
    gap = bool(s.get('closed_at') and now-s['closed_at'] != 1)
    values = (o.bar_open, o.bar_high, o.bar_low, o.price)
    valid = all(v is not None and isfinite(v) and v > 0 for v in values)
    valid = valid and o.bar_low <= min(o.bar_open, o.price) <= max(o.bar_open, o.price) <= o.bar_high
    state, reason = 'advance', 'episode_started' if new_episode else 'advance_maintained'
    if gap or not valid:
        # Never confirm a failed recovery using a missing or invalid candle.
        s.pop('pullback', None)
        s['structure'] = {}
        s['bodies'] = []
        previous = None
        state, reason = 'unresolved', 'candle_gap' if gap else 'invalid_candle'
    baseline = body_mean(s['bodies'], policy)
    structure = s['structure']
    if valid and episode is not None:
        structure['closed_atr'] = d.get('closed_atr', 0.)
        observe_structure(o, structure, True, policy, d.get('decision_levels', []), market_scope=True)
        swings = structure.get('position_structure') or {}
        candidate = swings.get('resistance') or {}
        established = swings.get('established_support') or {}
        support = candidate.get('support') or {}
        boundaries = [x['boundary'] for x in (support, established) if x.get('boundary') is not None]
        boundary = max(boundaries) if boundaries else None
        pb = s.get('pullback')
        retreat = o.price < o.bar_open or (previous is not None and o.price < previous)
        if not gap:
            if retreat and not pb:
                pb = dict(high=max(prior_high, o.bar_open), low=o.bar_low, started_at=now, recovery=False)
                s['pullback'] = pb
            if pb:
                if pb.get('recovery') and o.price < pb['floor']:
                    state, reason = 'rejection', 'failed_recovery_support_break'
                elif boundary is not None and o.price < boundary:
                    state, reason = 'rejection', 'resistance_support_break'
                elif o.price > pb['high'] and not retreat:
                    state, reason = 'continuation', 'pullback_high_reclaimed'
                    s.pop('pullback', None)
                elif retreat:
                    state, reason = 'pullback', 'retreat_support_unbroken'
                elif previous is not None and o.price > previous and o.price-pb['low'] > baseline*policy['detector_recovery_body_multiple']:
                    if not pb.get('recovery'):
                        pb.update(recovery=True, floor=pb['low'], recovery_at=now,
                                  required_rise=baseline*policy['detector_recovery_body_multiple'])
                    state, reason = 'recovery', 'support_held_recovery_attempt'
                else:
                    state, reason = ('recovery', 'recovery_unresolved') if pb.get('recovery') else ('pullback', 'pullback_unresolved')
                if not pb.get('recovery'):
                    pb['low'] = min(pb['low'], o.bar_low)
            elif boundary is not None and o.price < boundary:
                state, reason = 'rejection', 'established_support_break'
            elif o.price > prior_high and prior_high > 0:
                state, reason = 'advance', 'completed_high_reclaimed'
        s['high'] = max(prior_high, o.bar_open, o.price)
        s['bodies'] = [*s['bodies'], abs(o.price-o.bar_open)][-policy['adaptive_target_body_window']:]
    else:
        candidate, boundary = {}, None
    if valid and episode is None:
        state, reason = 'inactive', 'macd_episode_closed'
    if valid:
        s['close'] = o.price
    s.update(closed_at=now, observed_at=now, state=state, reason=reason, sequence=s['sequence']+1)
    pb = s.get('pullback') or {}
    s['decision'] = dict(contract='candle-state-detector-1', sequence=s['sequence'],
        state=state, reason=reason, action='reject' if state == 'rejection' else 'observe',
        effective_at=o.observed_at.isoformat(), candle_start=(o.observed_at-timedelta(seconds=1)).isoformat(),
        candle_end=o.observed_at.isoformat(), timeframe='1s', episode=episode,
        episode_changed=new_episode, reference_price=o.price, candle_open=o.bar_open,
        candle_high=o.bar_high, candle_low=o.bar_low, candle_close=o.price,
        prior_close=previous, prior_advance_high=prior_high, advance_high=s['high'],
        pullback_low=pb.get('low'), recovery_floor=pb.get('floor'),
        recovery_required_rise=pb.get('required_rise'), body_baseline=baseline,
        support_boundary=boundary, resistance={k:candidate[k] for k in ('lower','upper','source','confirmed_at') if k in candidate},
        entry_threshold=d.get('entry_high_threshold'), valid=valid)
    d['continuation_detector'] = s
