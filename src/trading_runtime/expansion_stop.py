"""Promote confirmed resistance to support after causal expansion evidence."""
from math import floor


def manage(o, p, state, current):
    d = state['v5_breakout_state']
    if o.position_quantity <= 0 or o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events:
        return current
    s = d.setdefault('expansion_stop', {})
    now = o.observed_at.timestamp()
    if now <= s.get('closed_at', 0):
        return max(current, s.get('stop', current))
    s['closed_at'] = now
    policy = p['episode_management']
    # Overlapping rows are one barrier, not evidence of multiple breaks.
    bands = []
    for r in sorted(d.get('crossed', []), key=lambda r: r['lower']):
        if bands and r['lower'] <= bands[-1]['upper']:
            bands[-1]['upper'] = max(bands[-1]['upper'], r['upper'])
        else:
            bands.append(dict(r))
    if bands:
        highest = bands[-1]
        old = s.get('resistance')
        if not old or highest['upper'] >= old['upper']:
            above = [r for r in d['decision_levels'] if r['lower'] > highest['upper']]
            next_lower = min((r['lower'] for r in above), default=None)
            s.update(resistance=highest, next_lower=next_lower, broken_at=now)
    resistance = s.get('resistance')
    candidates = []
    support = (d.get('position_structure') or {}).get('established_support')
    if support and support.get('boundary', 0) > 0:
        candidates.append((support['boundary'], dict(source='established_swing_support', support=support)))
    if resistance and o.price > resistance['upper']:
        gap = (s['next_lower']-resistance['upper']) if s.get('next_lower') else None
        progress = (o.price-resistance['upper'])/gap if gap and gap > 0 else 0.
        location = d.get('entry_close_location') or 0.
        multi = (len(bands) >= policy['expansion_stop_minimum_breaks']
                 and o.bar_open is not None and o.price > o.bar_open
                 and location >= policy['expansion_stop_close_location'])
        if progress >= policy['expansion_stop_gap_fraction'] or multi:
            tick = p['execution']['tick_size']
            volatility_buffer = max(0., d.get('closed_atr', 0.))*policy['expansion_stop_atr_multiple']
            if gap and gap > 0:
                volatility_buffer = min(volatility_buffer, gap*policy['expansion_stop_gap_cap'])
            buffer = max(tick, max(0., o.ask-o.bid), volatility_buffer)
            candidates.append((resistance['lower']-buffer, dict(source='promoted_resistance',
                resistance=dict(resistance), broken_at=s['broken_at'], confirmed_at=now,
                gap_progress=progress, distinct_breaks=len(bands), close_location=location,
                buffer=buffer, next_lower=s.get('next_lower'))))
    for proposed, evidence in candidates:
        tick = p['execution']['tick_size']
        proposed = floor(proposed/tick+1e-9)*tick
        if current < proposed < min(o.price, o.bid):
            current = proposed
            state['trailing_support_selection'] = dict(evidence, stop=proposed)
    s['stop'] = current
    return current
