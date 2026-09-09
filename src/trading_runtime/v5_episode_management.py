"""Opt-in episode management with explicit causal attempt evidence."""
from math import isfinite


DEFAULTS = dict(rejection_from_below=True, rejection_closes=1,
                rejection_atr_multiple=0., stop_atr_multiple=0., take_profit_fraction=1.,
                entry_on_close=False, entry_range_seconds=0.)


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
    for key in ('rejection_atr_multiple', 'stop_atr_multiple', 'entry_range_seconds'):
        if type(policy[key]) not in (int, float) or not isfinite(policy[key]) or policy[key] < 0:
            raise ValueError('ATR multiples must be finite and nonnegative')
    if policy['entry_range_seconds'] and not policy['entry_on_close']:
        raise ValueError('Recent-range confirmation requires completed-candle entry')
    if policy['entry_range_seconds'] > 3600:
        raise ValueError('Recent-range history is bounded to one hour')
    fraction = policy['take_profit_fraction']
    if type(fraction) not in (int, float) or not isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError('Target fraction must be positive and at most one')
    parameters['episode_management'] = policy


def observe_entry(o, d, closed, policy):
    seconds = policy['entry_range_seconds']
    now = o.observed_at.timestamp()
    if not seconds or not closed or now <= d.get('closed_at', 0):
        return
    history = [row for row in d.get('entry_range_history', []) if row[0] >= now-seconds]
    d['entry_range_high'] = max((row[1] for row in history), default=None)
    d['entry_range_samples'] = len(history)
    history.append([now, max(o.price, o.bar_high or o.price)])
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
