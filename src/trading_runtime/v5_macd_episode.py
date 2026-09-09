"""Price-normalized MACD episodes and completed resistance-break management.

Episode state belongs to the ticker; gap samples and protection belong to a
single acquired position. Only past, causally available levels are used.
"""
from math import floor, isfinite, isclose

from . import v5_breakout as v5, swing_gap
from .v5_hod_ladder import target

CONTRACT = 'swing-v5-macd-episode-1'


def strictly_below(price, boundary):
    # Ignore binary floating-point noise, not a trading-price buffer. Bands
    # retain their sub-tick precision; 3.53 and 3.5300000000000002 are equal.
    return price < boundary and not isclose(price, boundary, rel_tol=1e-12, abs_tol=0.)


def gap_bps(o):
    if o.price <= 0 or o.macd_line is None or o.macd_signal is None:
        return None
    value = (o.macd_line - o.macd_signal) / o.price * 10_000
    return value if isfinite(value) else None


def acquisition_valid(o, p):
    gap = gap_bps(o)
    settings = p['v5_breakout']
    return (gap is not None and gap >= settings['minimum_macd_gap_bps'] - 1e-9
            and o.execution_vwap is not None
            and o.price > o.execution_vwap * (1 + settings['vwap_offset_bps'] / 10_000))


def observe(o, p, state):
    now = o.observed_at.timestamp()
    d = dict(state.get('v5_breakout_state') or {'contract': p['v5_breakout_contract']})
    if now < d.get('observed_at', 0):
        return
    gap = gap_bps(o)
    opened = gap is not None and gap >= p['v5_breakout']['minimum_macd_gap_bps'] - 1e-9
    if gap is not None and not opened:
        if d.get('period_max', 0) > 0:
            d['previous_episode_body_high'] = d['period_max']
        if d.get('macd_open') or d.get('period_max', 0) > 0:
            d['episode_reset_at'] = o.observed_at.isoformat()
            d['episode_reset_gap_bps'] = gap
        d['period_max'] = 0.0
    prior = d.get('levels', [])
    current_levels = v5.rows(o, p)
    d.update(observed_at=now, macd_open=opened, macd_gap_bps=gap,
             prior_max=d.get('period_max', 0.0), decision_levels=prior, crossed=[])
    closed = o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events
    if p['v5_breakout_contract'] == v5.MACD_REJECTION_CONTRACT:
        observe_rejection(o, d, current_levels, closed)
    if closed and now > d.get('closed_at', 0):
        previous = d.get('closed_price')
        # Trade observations must not erase the last completed-bar witnesses
        # when the book changes role at the same close boundary.
        d['crossed'] = [r for r in d.get('closed_levels', prior)
                        if previous is not None and previous <= r['upper'] < o.price]
        if p['v5_breakout_contract'] == v5.MACD_REJECTION_CONTRACT:
            d['crossed'] = [r for r in d.get('closed_levels', prior)
                            if previous is not None and not strictly_below(r['upper'], previous)
                            and strictly_below(r['upper'], o.price)]
        if opened:
            d['period_max'] = max(d.get('period_max', 0.0), o.bar_open or o.price, o.price)
            # At the close boundary this candle is now part of the past.
            d['prior_max'] = d['period_max']
        d.update(closed_at=now, closed_price=o.price, closed_levels=current_levels)
        d['decision_levels'] = list({r['unified_level_id']: r for r in [*current_levels, *d['crossed']]}.values())
    d['levels'] = current_levels
    d['entry_high_threshold'] = d['prior_max'] * (1 + p['v5_breakout']['episode_high_offset_bps'] / 10_000)
    if o.position_quantity <= 0:
        for key in ('position_gaps', 'pending_target', 'fill_stop_initialized'):
            d.pop(key, None)
    state['v5_breakout_state'] = d


def observe_rejection(o, d, levels, closed):
    """Freeze a contacted band until a completed close resolves its attempt.

    Use actual observed prices while holding, not a candle high that might
    predate acquisition. A removed/role-changed level keeps its touch witness.
    """
    if o.position_quantity <= 0:
        d.pop('resistance_contacts', None)
        d.pop('resistance_rejection', None)
        return
    contacts = dict(d.get('resistance_contacts') or {})
    now = o.observed_at.timestamp()
    for level in levels:
        if not strictly_below(o.price, level['lower']) and not strictly_below(level['upper'], o.price):
            contacts.setdefault(str(level['unified_level_id']), {
                'level': dict(level), 'touched_at': o.observed_at.isoformat(), 'touch_price': o.price})
    if closed and now > d.get('closed_at', 0):
        rejected = [contact for contact in contacts.values() if strictly_below(o.price, contact['level']['lower'])]
        if rejected and not d.get('resistance_rejection'):
            witness = max(rejected, key=lambda row: row['level']['lower'])
            d['resistance_rejection'] = {**witness, 'confirmed_at': o.observed_at.isoformat(),
                                         'close_price': o.price, 'timeframe': '1s'}
        contacts = {key: contact for key, contact in contacts.items()
                    if not strictly_below(o.price, contact['level']['lower'])
                    and not strictly_below(contact['level']['upper'], o.price)}
    d['resistance_contacts'] = contacts


def initial_stop(o, p, entry_price):
    supports = [r for r in swing_gap.levels(o, {'minimum_p_norm': 0})
                if r['side'] == 1 and r.get('scale') == 'major' and r['upper'] < entry_price]
    if supports:
        support = max(supports, key=lambda r: (r['confirmed_at_ms'], r['created_at_ms'], r['unified_level_id']))
        return v5.below(support, p), {'source': 'outer_swing_low', 'level': support}
    tick = p['execution']['tick_size']
    stop = floor(entry_price * (1 - p['v5_breakout']['initial_stop_pct'] / 100) / tick + 1e-9) * tick
    return stop, {'source': 'entry_percent', 'percent': p['v5_breakout']['initial_stop_pct']}


def overhead(levels, price, p):
    return sorted((r for r in levels if r['lower'] > price and target(r, p)['price'] > price),
                  key=lambda r: (r['lower'], r['upper'], str(r['unified_level_id'])))


def select(o, p, state):
    d = state.get('v5_breakout_state', {})
    if not d.get('macd_open'):
        return {'reason': 'v5_macd_gap_below_minimum'}
    if not acquisition_valid(o, p):
        return {'reason': 'v5_price_not_above_vwap'}
    threshold = d.get('prior_max', 0.0) * (1 + p['v5_breakout']['episode_high_offset_bps'] / 10_000)
    if not strictly_below(threshold, o.price):
        return {'reason': 'v5_period_high_not_reclaimed'}
    levels = d.get('decision_levels', [])
    above = overhead(levels, max(o.price, o.ask), p)
    ordinal = p['v5_breakout']['initial_target_ordinal']
    if len(above) < ordinal:
        return {'reason': 'v5_second_target_unavailable'}
    selected = target(above[ordinal - 1], p)
    stop, stop_selection = initial_stop(o, p, o.price)
    if stop <= 0 or stop >= min(o.price, o.bid):
        return {'reason': 'v5_stop_already_triggered'}
    return dict(reason='', stop=stop, stop_selection=stop_selection,
                target=selected['price'], target_selection=selected,
                broken_at=o.observed_at.timestamp(), references=levels,
                session_high=o.structural_session_high, interval_based=True)


def sample_gaps(d, levels, anchor):
    """Freeze each adjacent level pair once during this position's lifetime."""
    samples = dict(d.get('position_gaps') or {})
    above = sorted((r for r in levels if r['lower'] > anchor['lower']),
                   key=lambda r: (r['lower'], r['upper'], str(r['unified_level_id'])))
    chain = [anchor, *above[:2]]
    for left, right in zip(chain, chain[1:]):
        key = str(left['unified_level_id']) + '->' + str(right['unified_level_id'])
        samples.setdefault(key, right['lower'] - left['lower'])
    d['position_gaps'] = samples
    return sum(samples.values()) / len(samples) if samples else None


def manage(o, p, state):
    d = state['v5_breakout_state']
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    if not d.get('fill_stop_initialized') and o.average_price > 0:
        # Freeze the selected swing at entry; do not discover a new initial
        # anchor after acquisition. Rebase only the percentage fallback.
        selection = state.get('v5_entry_selection', {}).get('stop_selection', {})
        if selection.get('source') == 'entry_percent':
            tick = p['execution']['tick_size']
            current = floor(o.average_price * (1 - p['v5_breakout']['initial_stop_pct'] / 100) / tick + 1e-9) * tick
        d['fill_stop_initialized'] = True
        entry_levels = state.get('v5_entry_selection', {}).get('references', d['decision_levels'])
        below = [r for r in entry_levels if r['upper'] < o.average_price]
        if below:
            sample_gaps(d, entry_levels, max(below, key=lambda r: r['upper']))
    for broken in d.get('crossed', []):
        current = max(current, v5.below(broken, p))
        average = sample_gaps(d, d['decision_levels'], broken)
        above = overhead(d['decision_levels'], max(o.price, o.ask), p)
        if average is None or len(above) < 2:
            continue
        reference = above[0]['lower'] + average
        chosen = min(above[1:], key=lambda r: (abs(r['lower'] - reference), r['lower']))
        selected = dict(target(chosen, p), average_gap=average, target_reference=reference,
                        broken_level_id=broken['unified_level_id'], confirmed_at=o.observed_at.isoformat())
        existing = max([*(state.get('structural_profit_targets') or [0]),
                        (d.get('pending_target') or {}).get('price', 0)])
        if selected['price'] > existing:
            d['pending_target'] = selected
    return current
