"""Causal setup evidence and execution budgets for the v3 gap candidate."""
from math import ceil, floor, isfinite

DEFAULTS = dict(cost_bps_per_side=5., slippage_ticks=1, maximum_support_distance_bps=500.,
                take_profit_fraction=.5, evidence_lifetime_seconds=30.)


def validate(settings):
    if any(not isinstance(v, (int, float)) or not isfinite(v) or v < 0 for v in settings.values()):
        raise ValueError('Invalid gap continuation settings')
    if not 0 < settings['take_profit_fraction'] < 1 or settings['maximum_support_distance_bps'] <= 0:
        raise ValueError('Invalid runner fraction or support distance')


def key(row):
    # Geometry survives load-time membership/id changes.
    return f"{row['lower']:.8f}:{row['upper']:.8f}"


def mature(row):
    return bool(row.get('structural_mature'))


def observe(observation, parameters, state, rows):
    """Only a later completed candle can confirm a touch or reclaim."""
    if 'bar_close' not in observation.evaluation_events or observation.source_timeframe not in ('', '1s'):
        return
    now = observation.observed_at.timestamp()
    book = dict(state.get('gap_evidence') or {})
    if now <= book.get('at', float('-inf')):
        return
    ttl = parameters['gap_continuation']['evidence_lifetime_seconds']
    touches = {k: dict(v) for k, v in book.get('touches', {}).items() if now-v['at'] <= ttl}
    failed = dict(book.get('failed') or {})
    price = observation.price
    tick = parameters['execution']['tick_size']
    low, high = observation.bar_low or price, observation.bar_high or price
    anchor = (state.get('trailing_support_selection') or state.get('gap_selection') or {}).get('support')
    if anchor and price <= float(state.get('active_stop') or 0):
        failed[key(anchor)] = now
    for row in rows:
        if row['side'] != 1:
            continue
        identity = key(row)
        old = touches.get(identity)
        if low <= row['upper'] and high >= row['lower']:
            if old is None or old.get('departed') or old['at'] <= failed.get(identity, float('-inf')):
                touches[identity] = dict(at=now, departed=False)
        elif old and now > old['at'] and price > row['upper']+tick:
            old['departed'] = True
            if old['at'] > failed.get(identity, float('inf')):
                failed.pop(identity, None)
    vwap = observation.execution_vwap
    if vwap and vwap > 0:
        previous = book.get('price')
        if low <= vwap <= high or (price <= vwap and previous is not None and previous >= vwap):
            touches['vwap'] = dict(at=now, departed=False)
        elif 'vwap' in touches and now > touches['vwap']['at'] and price > vwap+tick:
            touches['vwap']['departed'] = True
    state['gap_evidence'] = dict(at=now, price=price, touches=touches, failed=failed)


def select(obs, parameters, state, rows):
    from .swing_gap import resistance_clusters
    settings, policy = parameters['swing_gap'], parameters['gap_continuation']
    price, tick = obs.price, parameters['execution']['tick_size']
    if not all(isfinite(v) for v in (obs.bid, obs.ask)) or not 0 < obs.bid <= obs.ask:
        return dict(reason='gap_spread_unavailable')
    entry = obs.ask + policy['slippage_ticks']*tick
    failed = (state.get('gap_evidence') or {}).get('failed', {})
    supports = []
    for row in rows:
        stop = floor((row['lower']-tick+tick*1e-9)/tick)*tick
        if (row['side'] == 1 and mature(row) and key(row) not in failed and row['upper'] < price
                and 0 < stop <= price-settings['minimum_stop_distance']+1e-9
                and (entry-stop)/entry*10000 <= policy['maximum_support_distance_bps']):
            supports.append((stop, row))
    if not supports:
        return dict(reason='gap_validated_support_unavailable')
    stop, support = max(supports, key=lambda r:r[0])
    result = dict(stop=stop, support=support, executable_entry=entry, reason='gap_breakout_or_bounce_unconfirmed')
    if not obs.execution_vwap or price <= obs.execution_vwap:
        return dict(result, reason='gap_price_not_above_vwap')
    clusters = resistance_clusters(rows, price*settings['cluster_gap_bps']/10000)
    # All resistance is an obstacle, even if too immature to anchor a trade.
    if any(c['lower'] <= price <= c['upper'] for c in clusters):
        return dict(result, reason='gap_inside_resistance_cluster')
    above = next((c for c in clusters if c['lower'] > price), None)
    below = next((c for c in reversed(clusters) if c['upper'] < price), None)
    if above is None:
        return result
    touches = (state.get('gap_evidence') or {}).get('touches', {})
    rebound = any(touches.get(k, {}).get('departed') for k in (key(support), 'vwap'))
    if not price > float(obs.bar_open or price):
        return dict(result, reason='gap_upward_departure_unconfirmed')
    setup = None
    if below and price > below['upper'] and price-below['upper'] <= price*settings['proximity_bps']/10000:
        members = set(below['members'])
        if all(mature(r) for r in rows if str(r['unified_level_id']) in members):
            setup = dict(lower=below['upper'], upper=above['lower'], setup='confirmed_cluster_breakout')
    if setup is None and rebound:
        setup = dict(lower=support['upper'], upper=above['lower'], setup='confirmed_support_vwap_bounce')
    if setup is None:
        return result
    target = (ceil(above['lower']/tick-1e-9)-1)*tick
    cost = policy['cost_bps_per_side']/10000
    rr = settings['minimum_reward_risk']
    # Include costs on both outcomes. This ceiling travels with the broker order.
    ceiling = (target*(1-cost)+rr*stop*(1-cost))/((1+rr)*(1+cost))
    ceiling = floor((ceiling+tick*1e-9)/tick)*tick
    ceiling = min(ceiling, floor((entry+tick*1e-9)/tick)*tick)
    result.update(gap=setup, target=target, maximum_buy_price=ceiling,
        reward_risk=(target*(1-cost)-entry*(1+cost))/(entry*(1+cost)-stop*(1-cost)))
    if (setup['upper']-setup['lower'])/price*10000 < settings['minimum_gap_bps']:
        return dict(result, reason='gap_too_small')
    if entry > ceiling+1e-9 or target <= entry:
        return dict(result, reason='gap_executable_reward_risk_insufficient')
    return dict(result, reason='')


def ratchet(obs, parameters, state, rows):
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    entry_at = state.get('entry_at', '')
    from datetime import datetime
    since = datetime.fromisoformat(entry_at).timestamp()*1000 if entry_at else float('inf')
    tick = parameters['execution']['tick_size']
    choices = [r for r in rows if r['side'] == 1 and mature(r) and r['upper'] < obs.bid
               and r['confirmed_at_ms'] > since]
    if choices:
        row = max(choices, key=lambda r:r['lower'])
        candidate = floor((row['lower']-tick+tick*1e-9)/tick)*tick
        if candidate > current:
            state['trailing_support_selection'] = dict(support=row, stop=candidate)
            return candidate
    return current
