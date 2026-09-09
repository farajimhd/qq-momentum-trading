"""Opt-in episode management with explicit causal attempt evidence."""
from math import isfinite


DEFAULTS = dict(rejection_from_below=True, rejection_closes=1,
                rejection_atr_multiple=0., stop_atr_multiple=0., take_profit_fraction=1.,
                entry_on_close=False, entry_range_seconds=0.,
                profit_trail_atr_multiple=0., profit_trail_activation_atr=1.,
                entry_confirmation_window_ms=0., maximum_macd_line_bps=0.,
                entry_minimum_close_location=0.)


def configure(parameters):
    raw = parameters.get('episode_management')
    if raw is None:
        return
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('Unknown episode management setting')
    policy = dict(DEFAULTS, **raw)
    if any(type(policy[key]) is not bool for key in ('rejection_from_below','entry_on_close')):
        raise ValueError('Episode policy flags must be boolean')
    if type(policy['rejection_closes']) is not int or not 1 <= policy['rejection_closes'] <= 60:
        raise ValueError('Rejection confirmation must be one to sixty completed candles')
    for key in ('rejection_atr_multiple', 'stop_atr_multiple', 'entry_range_seconds',
                'profit_trail_atr_multiple', 'profit_trail_activation_atr', 'entry_confirmation_window_ms',
                'maximum_macd_line_bps', 'entry_minimum_close_location'):
        if type(policy[key]) not in (int, float) or not isfinite(policy[key]) or policy[key] < 0:
            raise ValueError('ATR multiples must be finite and nonnegative')
    if policy['entry_range_seconds'] > 3600:
        raise ValueError('Recent-range history is bounded to one hour')
    if policy['entry_confirmation_window_ms'] > 1000:
        raise ValueError('Entry confirmation cannot outlive its one-second candle interval')
    if policy['entry_confirmation_window_ms'] and not policy['entry_on_close']:
        raise ValueError('An execution window requires completed-candle entry confirmation')
    if policy['entry_minimum_close_location'] > 1:
        raise ValueError('Close location must be between zero and one')
    if policy['entry_minimum_close_location'] and not policy['entry_on_close']:
        raise ValueError('Close location requires completed-candle entry confirmation')
    fraction = policy['take_profit_fraction']
    if type(fraction) not in (int, float) or not isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Target fraction must be between zero and one')
    parameters['episode_management'] = policy


def profit_trail(o, d, state, policy, tick, current):
    """Optional close-only profit protection, scoped to one acquired lifecycle."""
    from math import floor
    multiple = policy.get('profit_trail_atr_multiple', 0.)
    if not multiple or o.position_quantity <= 0 or o.average_price <= 0:
        return current
    if not (o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events):
        return current
    now = o.observed_at.timestamp()
    trail = dict(d.get('profit_trail') or {})
    if now <= trail.get('observed_at', 0):
        return max(current, trail.get('stop', current))
    atr = d.get('closed_atr', 0.)
    entry_atr = state.get('v5_entry_selection', {}).get('entry_atr', 0.)
    if atr <= 0 or entry_atr <= 0:
        return current
    peak = max(trail.get('peak_close', o.average_price), o.price)
    armed = trail.get('armed', False) or peak > o.average_price + policy['profit_trail_activation_atr']*entry_atr
    proposed = floor((peak-multiple*atr)/tick+1e-9)*tick if armed else current
    current = max(current, proposed, trail.get('stop', current))
    d['profit_trail'] = dict(observed_at=now, peak_close=peak, armed=armed,
        entry_atr=entry_atr, current_atr=atr, stop=current, confirmed_at=o.observed_at.isoformat())
    return current


def observe_entry(o, d, closed, policy):
    seconds = policy['entry_range_seconds']
    now = o.observed_at.timestamp()
    if not seconds or not closed or now <= d.get('closed_at', 0):
        return
    history = [row for row in d.get('entry_range_history', []) if row[0] >= now-seconds]
    history.append([now, max(o.price, o.bar_high or o.price)])
    reference = history[:-1] if policy['entry_on_close'] else history
    d['entry_range_high'] = max((row[1] for row in reference), default=None)
    d['entry_range_samples'] = len(reference)
    d['entry_range_history'] = history


def observe_rejection(o, d, levels, closed, policy):
    from .v5_macd_episode import strictly_below
    previous = d.get('attempt_previous_price')
    d['attempt_previous_price'] = o.price
    if o.position_quantity <= 0:
        d.pop('resistance_contacts', None)
        d.pop('resistance_rejection', None)
        return
    contacts = dict(d.get('resistance_contacts') or {})
    for level in levels:
        in_band = not strictly_below(o.price, level['lower']) and not strictly_below(level['upper'], o.price)
        approached = previous is not None and strictly_below(previous, level['lower'])
        volatility_ready = not policy['rejection_atr_multiple'] or d.get('closed_atr', 0.) > 0
        if in_band and volatility_ready and (approached or not policy['rejection_from_below']):
            contacts.setdefault(str(level['unified_level_id']), dict(
                level=dict(level), touched_at=o.observed_at.isoformat(), touch_price=o.price,
                approach_price=previous, below_closes=0,
                rejection_boundary=level['lower'] - policy['rejection_atr_multiple'] * d.get('closed_atr', 0.)))
    if closed and o.observed_at.timestamp() > d.get('closed_at', 0):
        retained = {}
        for key, contact in contacts.items():
            contact = dict(contact)
            level = contact['level']
            if strictly_below(level['upper'], o.price):
                continue
            contact['below_closes'] = contact.get('below_closes', 0)+1 if strictly_below(
                o.price, contact['rejection_boundary']) else 0
            if contact['below_closes'] >= policy['rejection_closes']:
                if not d.get('resistance_rejection'):
                    d['resistance_rejection'] = dict(contact, confirmed_at=o.observed_at.isoformat(),
                        close_price=o.price, timeframe='1s', required_closes=policy['rejection_closes'])
            else:
                retained[key] = contact
        contacts = retained
    d['resistance_contacts'] = contacts
