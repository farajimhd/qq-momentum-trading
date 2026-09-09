"""Opt-in causal gap trades, with fixed structural protection and target."""
from math import ceil, floor, isfinite
from itertools import chain

from src.market_engine.structure_gaps import gaps

LEGACY_CONTRACT = 'swing-v4-gap-v1'
CLUSTER_CONTRACT = 'swing-v4-gap-v2'
CONTRACT = 'swing-v4-gap-v3'
DEFAULTS = dict(minimum_p_norm=.2, minimum_stop_distance=.10,
                minimum_gap_bps=20., proximity_bps=50., minimum_reward_risk=1., cluster_gap_bps=50.)


def runner_policy(parameters):
    if parameters.get('swing_gap_contract') == CONTRACT:
        from .gap_continuation import DEFAULTS as continuation_defaults
        return parameters.get('gap_continuation', continuation_defaults)
    policy = parameters.get('gap_management') or {}
    return policy if parameters.get('swing_gap_contract') and policy.get('enabled') else None


def tracks_reclaims(parameters):
    return bool(parameters.get('swing_gap_contract') and (
        parameters['swing_gap_contract'] == CONTRACT or parameters.get('gap_reclaim_failed_support')))


def configure(parameters):
    from . import entry_body
    if parameters['swing_gap_contract'] not in (CONTRACT, CLUSTER_CONTRACT, LEGACY_CONTRACT) or not parameters.get('swing_evidence_contract'):
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
    entry_body.configure(parameters)
    if parameters.get('gap_management', {}).get('enabled'):
        fraction = parameters['gap_management'].get('take_profit_fraction')
        if not isinstance(fraction, (int, float)) or not isfinite(fraction) or not 0 < fraction < 1:
            raise ValueError('Gap management requires a partial target fraction between zero and one')
    if tracks_reclaims(parameters):
        from . import gap_continuation
        parameters['gap_continuation'] = dict(gap_continuation.DEFAULTS, **parameters.get('gap_continuation', {}))
        gap_continuation.validate(parameters['gap_continuation'])


def resistance_clusters(rows, maximum_gap):
    """Group nearby resistance bands for selection without changing the book.

    Each cluster retains its outer bounds and member identities. Never merge
    support into resistance or bridge a gap larger than the configured distance.
    """
    clusters = []
    for row in sorted((r for r in rows if r['side'] == -1), key=lambda r: (r['lower'], r['upper'], str(r['unified_level_id']))):
        if clusters and row['lower'] <= clusters[-1]['upper'] + maximum_gap + 1e-12:
            cluster = clusters[-1]
            cluster['upper'] = max(cluster['upper'], row['upper'])
            cluster['members'].append(str(row['unified_level_id']))
            cluster['unified_level_id'] = '|'.join(cluster['members'])
        else:
            clusters.append(dict(lower=row['lower'], upper=row['upper'], side=-1,
                members=[str(row['unified_level_id'])], unified_level_id=str(row['unified_level_id'])))
    return clusters


def levels(observation, settings, *, side=None):
    """Validate causal levels, optionally selecting one side before projection.

    Side filtering uses each row's declared role, not its containing tuple.
    Copy accepted rows only, keeping caller-owned structural evidence immutable.
    """
    if side not in (None, -1, 1):
        raise ValueError('Structural side must be support, resistance or both')
    now = observation.observed_at.timestamp() * 1000
    valid = {}
    for raw in chain(observation.structural_support_levels, observation.structural_resistance_levels):
        try:
            row = raw if isinstance(raw, dict) else dict(raw)
            if side is not None and row.get('side') != side:
                continue
            lower = float(row.get('band_lower', row.get('lower')))
            upper = float(row.get('band_upper', row.get('upper')))
            v5 = row.get('book_version') == 'causal-swing-closing-book-5'
            score = 1. if v5 else float(row['p_norm'])
            created, confirmed = float(row['created_at_ms']), float(row['confirmed_at_ms'])
            retained = (settings.get('include_retained_resistances') and v5 and row.get('side')==-1
                        and row.get('retained_qualified_resistance') is True
                        and row.get('lifecycle') in ('awaiting_retest','retest_contact'))
            if (row.get('book_version') not in ('causal-swing-closing-book-4','causal-swing-closing-book-5') or (row.get('lifecycle') != 'active' and not retained)
                    or row.get('side') not in (1, -1)
                    or not all(isfinite(v) for v in (lower, upper, score, created, confirmed))
                    or not 0 < lower <= upper or max(created, confirmed) > now
                    or not settings['minimum_p_norm'] <= score <= 1):
                continue
            if v5 and row['side']==-1 and (not isfinite(float(row.get('selection_score',0))) or float(row.get('selection_score',0))<30):
                continue
            valid[(row['side'], str(row['unified_level_id']))] = dict(row, lower=lower, upper=upper)
        except (KeyError, TypeError, ValueError):
            continue
    return list(valid.values())


def select(observation, parameters, state=None):
    settings = parameters['swing_gap']
    price = observation.price
    tick = float(parameters['execution']['tick_size'])
    if not isfinite(price) or price <= 0 or tick <= 0:
        return dict(reason='gap_invalid_price')
    rows = levels(observation, settings)
    if parameters['swing_gap_contract'] == CONTRACT:
        from .gap_continuation import select as continuation_select
        return continuation_select(observation, parameters, state or {}, rows)
    eligible = []
    for row in rows:
        stop = floor((row['lower'] - tick + tick * 1e-9) / tick) * tick
        if row['side'] == 1 and row['upper'] < price and 0 < stop <= price-settings['minimum_stop_distance']+1e-9:
            eligible.append((stop, row))
    if not eligible:
        return dict(reason='gap_support_minimum_distance_unavailable')
    stop, support = max(eligible, key=lambda item: item[0])
    result = dict(stop=stop, support=support, reason='gap_setup_unavailable')
    if parameters.get('gap_reclaim_failed_support'):
        from .gap_continuation import key
        if key(support) in ((state or {}).get('gap_evidence') or {}).get('failed', {}):
            return dict(result, reason='gap_failed_support_waiting_reclaim')
    vwap = observation.execution_vwap
    if vwap is None or not isfinite(vwap) or price <= vwap:
        return dict(result, reason='gap_price_not_above_vwap')
    proximity = price * settings['proximity_bps'] / 10000
    corrected = parameters['swing_gap_contract'] == CLUSTER_CONTRACT
    clusters = resistance_clusters(rows, price*settings['cluster_gap_bps']/10000) if corrected else []
    # The gap remains measured between outer band edges. Approach starts at
    # the bottom of its entrance cluster, not only the cluster's top edge.
    candidates = [g for g in gaps(clusters if corrected else rows) if g['kind'] == 'resistance' and g['upper'] > price]
    candidate = min(candidates, key=lambda g: g['lower'], default=None)
    selected = None
    entrance = next((c for c in clusters if candidate and c['upper'] == candidate['lower']), None)
    approach = entrance['lower'] if entrance else candidate['lower'] if candidate else 0
    if candidate and approach-proximity <= price < candidate['upper']:
        selected = dict(candidate, setup='resistance_gap')
        if entrance:
            selected['entrance_cluster'] = entrance
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
    # Keep quote validation, but do not subtract spread from a structural target.
    bid, ask = observation.bid, observation.ask
    if not all(isfinite(v) for v in (bid, ask)) or not 0 < bid <= ask:
        return dict(result, reason='gap_spread_unavailable')
    if parameters.get('gap_require_valid_stop_on_entry') and bid <= stop:
        return dict(result, reason='gap_stop_already_triggered')
    target = ((ceil(selected['upper']/tick-1e-9)-1)*tick if corrected else
              floor((selected['upper']-max(tick, ask-bid)+tick*1e-9)/tick)*tick)
    result.update(gap=selected, target=target, reward_risk=(target-price)/(price-stop))
    if (selected['upper']-selected['lower'])/price*10000 < settings['minimum_gap_bps']:
        return dict(result, reason='gap_too_small')
    if target-price < settings['minimum_reward_risk']*(price-stop) or target <= price:
        return dict(result, reason='gap_insufficient_reward_risk')
    return dict(result, reason='', gap=selected, target=target,
                reward_risk=(target-price)/(price-stop))
