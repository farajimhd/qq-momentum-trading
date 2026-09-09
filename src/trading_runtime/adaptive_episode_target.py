"""Causal, bounded episode statistics for resistance-based broker targets."""
from math import isfinite


def body_mean(values, policy):
    half_life = policy.get('adaptive_target_body_half_life', 0.)
    if not values:
        return 0.
    weights = [2**(-age/half_life) if half_life else 1. for age in reversed(range(len(values)))]
    return sum(v*w for v, w in zip(values, weights))/sum(weights)


def observe(o, d, policy):
    if not d.get('macd_open'):
        d.pop('adaptive_target', None)
        return
    episode = d.get('episode_started_at')
    s = d.setdefault('adaptive_target', {})
    if s.get('episode') != episode:
        s.clear()
        s.update(episode=episode, bodies=[], gaps=[], contraction_closes=0, stall_closes=0)
    now = o.observed_at.timestamp()
    if o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events or now <= s.get('closed_at', 0):
        return
    if o.bar_open is None or not isfinite(o.bar_open) or o.bar_open <= 0:
        return
    if s.get('closed_at') and now-s['closed_at'] != 1:
        s['contraction_closes'] = s['stall_closes'] = 0
    bodies = s['bodies']
    baseline = body_mean(bodies, policy)
    body = max(0., o.price-o.bar_open)
    contracting = baseline > 0 and body < baseline*policy['adaptive_target_contraction_ratio']
    s['contraction_closes'] = s['contraction_closes']+1 if contracting else 0
    s['bodies'] = [*bodies, body][-policy['adaptive_target_body_window']:]
    resistance = (d.get('position_structure') or {}).get('resistance') or {}
    key = [resistance.get('source'), resistance.get('level_id'), resistance.get('pivot_at'), resistance.get('upper')]
    stalled = bool(resistance) and o.price <= resistance['upper']
    s['stall_closes'] = (s['stall_closes']+1 if s.get('stall_key') == key else 1) if stalled else 0
    s['stall_key'] = key
    s['paused'] = max(s['contraction_closes'], s['stall_closes']) >= policy['adaptive_target_exhaustion_closes']
    # Include every crossed band's next two gaps. IDs deduplicate overlapping
    # chains within the bounded window; no event-rate-sized history is retained.
    levels = sorted(d.get('decision_levels', []), key=lambda r: (r['lower'], str(r['unified_level_id'])))
    gaps = {row[0]: row[1] for row in s['gaps']}
    for broken in sorted(d.get('crossed', []), key=lambda r: r['lower']):
        above = [r for r in levels if r['lower'] > broken['lower']][:2]
        chain = [broken, *above]
        for left, right in zip(chain, chain[1:]):
            key = str(left['unified_level_id'])+'->'+str(right['unified_level_id'])
            gaps.setdefault(key, right['lower']-left['lower'])
    s['gaps'] = list(gaps.items())[-policy['adaptive_target_gap_window']:]
    s['closed_at'] = now


def select(above, d, p):
    from .v5_hod_ladder import target

    policy = p['episode_management']
    s = d.get('adaptive_target') or {}
    bodies = s.get('bodies', [])
    gaps = [row[1] for row in s.get('gaps', [])]
    # Initial entry may precede the first confirmed resistance break.
    if not gaps:
        gaps = [b['lower']-a['lower'] for a, b in zip(above, above[1:])
                if b['lower'] > a['lower']][:policy['adaptive_target_gap_window']]
    mean_body = body_mean(bodies, policy)
    mean_gap = sum(gaps)/len(gaps) if gaps else 0.
    distance = max(mean_gap*policy['adaptive_target_gap_multiple'],
                   mean_body*policy['adaptive_target_body_multiple'])
    reference = above[0]['lower']+distance
    eligible = above[max(1, p['v5_breakout']['initial_target_ordinal']-1):]
    if not eligible:
        return None
    # Compare executable target prices, not band centers. Never select a lower
    # resistance merely because it is nearer to the desired distance.
    choices = [target(r, p) for r in eligible]
    selected = next((r for r in choices if r['price'] >= reference), choices[-1])
    return dict(selected, adaptive_contract='episode-expansion-target-1',
                average_gap=mean_gap, average_bullish_body=mean_body,
                body_half_life=policy.get('adaptive_target_body_half_life', 0.),
                body_samples=len(bodies), gap_samples=len(gaps), target_reference=reference,
                book_limited=selected['price'] < reference, episode=s.get('episode'),
                statistics_closed_at=s.get('closed_at'))
