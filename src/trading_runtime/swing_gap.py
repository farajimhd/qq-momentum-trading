"""Opt-in causal gap trades, with fixed structural protection and target."""
from math import floor, isfinite

from src.market_engine.structure_gaps import gaps

CONTRACT = 'swing-v4-gap-v1'
DEFAULTS = dict(minimum_p_norm=.2, minimum_stop_distance=.10,
                minimum_gap_bps=20., proximity_bps=50., minimum_reward_risk=1.)


def configure(parameters):
    if parameters['swing_gap_contract'] != CONTRACT or not parameters.get('swing_evidence_contract'):
        raise ValueError('Gap strategy requires the causal MACD evidence contract')
    if parameters.get('swing_momentum_contract'):
        raise ValueError('Gap and momentum policies must use separate candidates')
    settings = dict(DEFAULTS, **parameters.get('swing_gap', {}))
    if any(not isinstance(v, (int, float)) or not isfinite(v) or v < 0 for v in settings.values()):
        raise ValueError('Invalid gap settings')
    if settings['minimum_p_norm'] > 1 or settings['minimum_stop_distance'] < .10:
        raise ValueError('Gap stop must be at least ten cents below entry')
    parameters['swing_gap'] = settings
    parameters['protection']['stop'].update(method='structure', cap_initial_stop_distance=False)
    parameters['protection']['trailing'].update(enabled=False, mode='qualified_support')
    parameters['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=True)
    parameters['momentum_management']['macd_backstop']['enabled'] = False


def levels(observation, settings):
    now = observation.observed_at.timestamp() * 1000
    valid = {}
    for raw in (*observation.structural_support_levels, *observation.structural_resistance_levels):
        try:
            row = dict(raw)
            lower = float(row.get('band_lower', row.get('lower')))
            upper = float(row.get('band_upper', row.get('upper')))
            score = float(row['p_norm'])
            created, confirmed = float(row['created_at_ms']), float(row['confirmed_at_ms'])
            if (row.get('book_version') != 'causal-swing-closing-book-4' or row.get('lifecycle') != 'active'
                    or row.get('side') not in (1, -1)
                    or not all(isfinite(v) for v in (lower, upper, score, created, confirmed))
                    or not 0 < lower <= upper or max(created, confirmed) > now
                    or not settings['minimum_p_norm'] <= score <= 1):
                continue
            row.update(lower=lower, upper=upper)
            valid[(row['side'], str(row['unified_level_id']))] = row
        except (KeyError, TypeError, ValueError):
            continue
    return list(valid.values())


def select(observation, parameters):
    settings = parameters['swing_gap']
    price = observation.price
    tick = float(parameters['execution']['tick_size'])
    if not isfinite(price) or price <= 0 or tick <= 0:
        return dict(reason='gap_invalid_price')
    rows = levels(observation, settings)
    eligible = []
    for row in rows:
        stop = floor((row['lower'] - tick + tick * 1e-9) / tick) * tick
        if row['side'] == 1 and row['upper'] < price and 0 < stop <= price-settings['minimum_stop_distance']+1e-9:
            eligible.append((stop, row))
    if not eligible:
        return dict(reason='gap_support_minimum_distance_unavailable')
    stop, support = max(eligible, key=lambda item: item[0])
    result = dict(stop=stop, support=support, reason='gap_setup_unavailable')
    vwap = observation.execution_vwap
    if vwap is None or not isfinite(vwap) or price <= vwap:
        return dict(result, reason='gap_price_not_above_vwap')
    proximity = price * settings['proximity_bps'] / 10000
    # Same-side gap union is shared with the chart. Only the closest entrance
    # can qualify; do not jump across intervening resistance clusters.
    candidates = [g for g in gaps(rows) if g['kind'] == 'resistance' and g['upper'] > price]
    candidate = min(candidates, key=lambda g: g['lower'], default=None)
    selected = None
    if candidate and candidate['lower']-proximity <= price < candidate['upper']:
        selected = dict(candidate, setup='resistance_gap')
    else:
        # A green observation near a support or VWAP supplies rebound evidence.
        opening = float(observation.bar_open or 0)
        floors = [r for r in rows if r['side'] == 1 and 0 <= price-r['upper'] <= proximity]
        rebound = price > opening > 0 and (floors or 0 <= price-vwap <= proximity)
        resistance = min((r for r in rows if r['side'] == -1 and r['upper'] >= price),
                         key=lambda r: r['lower'], default=None)
        if rebound and resistance and resistance['lower'] > price:
            selected = dict(lower=max([vwap, *[r['upper'] for r in floors]]),
                            upper=resistance['lower'], setup='support_vwap_rebound')
    if not selected:
        return result
    # Reserve one spread before resistance; round down to an executable tick.
    bid, ask = observation.bid, observation.ask
    if not all(isfinite(v) for v in (bid, ask)) or not 0 < bid <= ask:
        return dict(result, reason='gap_spread_unavailable')
    buffer = max(tick, ask-bid)
    target = floor((selected['upper']-buffer+tick*1e-9)/tick)*tick
    if (selected['upper']-selected['lower'])/price*10000 < settings['minimum_gap_bps']:
        return dict(result, reason='gap_too_small')
    if target-price < settings['minimum_reward_risk']*(price-stop) or target <= price:
        return dict(result, reason='gap_insufficient_reward_risk')
    return dict(result, reason='', gap=selected, target=target,
                reward_risk=(target-price)/(price-stop))
