"""Bounded, causal structure belonging to one acquired position.

Pivots become available only after their right-hand candles close. This is a
position management projection, not a replacement for the shared V5 level book.
"""
from math import isfinite


def observe(o, d, closed, policy, levels, *, market_scope=False):
    from .v5_macd_episode import strictly_below

    if not market_scope and o.position_quantity <= 0:
        d.pop('position_structure', None)
        d.pop('position_structure_failure', None)
        return
    now = o.observed_at.timestamp()
    s = d.setdefault('position_structure', dict(contract='position-swings-1',
        acquired_observed_at=now, bars=[]))
    # Exclude the candle spanning first acquisition, even if first seen at its
    # close. Quotes and duplicate closes cannot confirm or erase a pivot.
    if not closed or now <= s.get('closed_at', 0) or (not market_scope and now-1 < s['acquired_observed_at']):
        return
    values = (o.bar_open, o.bar_high, o.bar_low, o.price)
    if any(v is None or not isfinite(v) or v <= 0 for v in values):
        return
    if not o.bar_low <= min(o.bar_open, o.price) <= max(o.bar_open, o.price) <= o.bar_high:
        return
    left, right = policy['swing_left_bars'], policy['swing_right_bars']
    bars = s['bars']
    if s.get('closed_at') and now-s['closed_at'] != 1:
        # A data gap cannot count as the missing confirmation candles.
        bars = []
        s['failure_closes'] = 0
    bar = dict(at=now, high=o.bar_high, low=o.bar_low, open=o.bar_open, close=o.price)
    bars = [*bars, bar][-(left+right+1):]
    s.update(bars=bars, closed_at=now, developing_high=max(b['high'] for b in bars),
             developing_low=min(b['low'] for b in bars))
    atr = d.get('closed_atr', 0.)
    buffer = max(policy['swing_buffer_bps']*o.price/10000,
                 policy['swing_buffer_atr_multiple']*atr)
    if policy['swing_buffer_atr_multiple'] and atr <= 0:
        return
    # Existing resistance resolves only through a strict completed upper break.
    resistance = s.get('resistance')
    if resistance and strictly_below(resistance['upper'], o.price):
        support = s.get('low')
        if support and support['at'] < now and support['low'] < o.price:
            old = s.get('established_support')
            if not old or support['low'] > old['low']:
                s['established_support'] = dict(support, established_at=now,
                    break_resistance=dict(resistance), boundary=support['low']-buffer)
        s.pop('resistance', None)
    if len(bars) == left+right+1:
        pivot = bars[left]
        neighbors = bars[:left]+bars[left+1:]
        high = all(strictly_below(b['high'], pivot['high']) for b in neighbors)
        low = all(strictly_below(pivot['low'], b['low']) for b in neighbors)
        # An outside candle with both extremes has no known intrabar ordering.
        if high and not low and pivot['high']-min(b['low'] for b in bars[left+1:]) > buffer:
            s['high'] = dict(pivot, confirmed_at=now)
            anchor = s.get('low')
            s['resistance'] = dict(lower=max(pivot['open'], pivot['close']), upper=pivot['high'],
                pivot_at=pivot['at'], confirmed_at=now, source='internal_swing',
                support=dict(anchor, boundary=anchor['low']-buffer) if anchor and anchor['at'] < pivot['at'] else None)
        elif low and not high and max(b['high'] for b in bars[left+1:])-pivot['low'] > buffer:
            s['low'] = dict(pivot, confirmed_at=now)
    # A V5 contact also needs a previously confirmed internal support witness.
    # Keep its original bounds if the book subsequently changes role.
    if not s.get('resistance') and s.get('low'):
        contacted = [r for r in levels if bar['high'] >= r['lower'] and
                     not strictly_below(r['upper'], o.price) and bars[0]['at'] < now and
                     len(bars) > 1 and strictly_below(bars[-2]['close'], r['lower'])]
        if contacted:
            r = min(contacted, key=lambda r: (r['lower'], str(r['unified_level_id'])))
            s['resistance'] = dict(lower=r['lower'], upper=r['upper'], source='v5',
                level_id=str(r['unified_level_id']), confirmed_at=now,
                support=dict(s['low'], boundary=s['low']['low']-buffer))
    resistance = s.get('resistance') or {}
    support = resistance.get('support')
    established = s.get('established_support')
    failed = support if support and strictly_below(o.price, support['boundary']) else None
    reason = 'resistance_failure'
    if established and strictly_below(o.price, established['boundary']):
        failed, reason = established, 'established_support_failure'
    failure_key = [reason, failed['at'], failed['boundary']] if failed else None
    s['failure_closes'] = (s.get('failure_closes', 0)+1
                          if failed and s.get('failure_key') == failure_key else 1 if failed else 0)
    s['failure_key'] = failure_key
    if failed and s['failure_closes'] >= policy['rejection_closes']:
        d.setdefault('position_structure_failure', dict(reason=reason, support=dict(failed),
            resistance=dict(resistance), confirmed_at=o.observed_at.isoformat(),
            close_price=o.price, timeframe='1s', required_closes=policy['rejection_closes']))
