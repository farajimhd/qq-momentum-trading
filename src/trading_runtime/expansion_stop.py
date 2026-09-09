"""Promote confirmed resistance to support after causal expansion evidence."""
from math import floor, ceil


def observe_break(o, d):
    """Ticker-level witness survives entry; only completed causal breaks qualify."""
    if o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events:
        return
    witness = d.get('confirmed_stop_break')
    if witness and o.price <= witness['resistance']['upper']:
        d.pop('confirmed_stop_break', None)
    bands = merged_breaks(d.get('crossed', []))
    if bands:
        highest = bands[-1]
        d['confirmed_stop_break'] = dict(resistance=highest, broken_at=o.observed_at.timestamp(),
            next_lower=min((r['lower'] for r in d['decision_levels'] if r['lower'] > highest['upper']), default=None))


def merged_breaks(rows):
    bands = []
    for r in sorted(rows, key=lambda r: r['lower']):
        if bands and r['lower'] <= bands[-1]['upper']:
            bands[-1]['upper'] = max(bands[-1]['upper'], r['upper'])
        else:
            bands.append(dict(r))
    return bands


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
    defensive = policy.get('defensive_structure_enabled')
    if defensive and not s.get('resistance') and d.get('confirmed_stop_break'):
        s.update(d['confirmed_stop_break'])
    # Overlapping rows are one barrier, not evidence of multiple breaks.
    bands = merged_breaks(d.get('crossed', []))
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
    exhausted = defensive and bool((d.get('adaptive_target') or {}).get('paused'))
    if resistance and (o.price > resistance['upper'] or exhausted):
        gap = (s['next_lower']-resistance['upper']) if s.get('next_lower') else None
        progress = (o.price-resistance['upper'])/gap if gap and gap > 0 else 0.
        location = d.get('entry_close_location') or 0.
        multi = (len(bands) >= policy['expansion_stop_minimum_breaks']
                 and o.bar_open is not None and o.price > o.bar_open
                 and location >= policy['expansion_stop_close_location'])
        if progress >= policy['expansion_stop_gap_fraction'] or multi or exhausted:
            tick = p['execution']['tick_size']
            volatility_buffer = max(0., d.get('closed_atr', 0.))*policy['expansion_stop_atr_multiple']
            if gap and gap > 0:
                volatility_buffer = min(volatility_buffer, gap*policy['expansion_stop_gap_cap'])
            buffer = max(tick, max(0., o.ask-o.bid), volatility_buffer)
            if defensive:
                # Strategy boundaries use structural prices, never the spread.
                boundary = resistance['lower']*(1-policy['structural_stop_offset_bps']/10000)
                structural_stop = min(floor(boundary/tick+1e-9)*tick,
                                      (ceil(resistance['lower']/tick-1e-9)-1)*tick)
                buffer = resistance['lower']-structural_stop
            candidates.append((resistance['lower']-buffer, dict(source='promoted_resistance',
                resistance=dict(resistance), broken_at=s['broken_at'], confirmed_at=now,
                gap_progress=progress, distinct_breaks=len(bands), close_location=location,
                buffer=buffer, next_lower=s.get('next_lower'), exhaustion=bool(exhausted))))
    for proposed, evidence in candidates:
        tick = p['execution']['tick_size']
        proposed = floor(proposed/tick+1e-9)*tick
        if defensive and proposed > current and proposed >= o.price:
            d['defensive_structure_failure'] = dict(evidence, reason='defensive_stop_already_breached',
                stop=proposed, confirmed_at=o.observed_at.isoformat())
            continue
        if current < proposed < (o.price if defensive else min(o.price, o.bid)):
            current = proposed
            state['trailing_support_selection'] = dict(evidence, stop=proposed)
    s['stop'] = current
    return current
