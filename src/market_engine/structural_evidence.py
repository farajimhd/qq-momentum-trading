"""Direction-symmetric, completed-candle evidence used by the indicator.

Shapes describe geometry, not predicted reversals. Retests require a later
candle than the break; OHLC cannot establish an intrabar break/retest order.
"""
from copy import deepcopy


def direction(level):
    return 1 if level['side'] in (-1, 'resistance') else -1


def level_evidence(level):
    return {k:level[k] for k in ('unified_level_id','level_id','side','lower','upper',
        'price','pivot_at','created_at_ms','confirmed_at','confirmed_at_ms','book_version') if k in level}


def morphology(bar, previous, baseline, settings):
    span = bar['high']-bar['low']
    body = abs(bar['close']-bar['open'])
    upper = bar['high']-max(bar['open'], bar['close'])
    lower = min(bar['open'], bar['close'])-bar['low']
    tags = []
    if span == 0:
        tags.append('flat')
    else:
        if upper/span >= settings.tail_range_fraction:
            tags.append('upper_tail')
        if lower/span >= settings.tail_range_fraction:
            tags.append('lower_tail')
        if body/span <= settings.indecision_body_fraction:
            tags.append('indecision')
        if body >= baseline*settings.expansion_body_multiple and body/span >= settings.expansion_body_fraction:
            tags.append('bullish_expansion' if bar['close']>bar['open'] else 'bearish_expansion')
    if previous:
        if bar['high']<=previous['high'] and bar['low']>=previous['low']:
            tags.append('inside')
        elif bar['high']>=previous['high'] and bar['low']<=previous['low']:
            tags.append('outside')
        if (bar['close']-bar['open'])*(previous['close']-previous['open'])<0 and min(bar['open'],bar['close'])<=min(previous['open'],previous['close']) and max(bar['open'],bar['close'])>=max(previous['open'],previous['close']):
            tags.append('bullish_engulfing' if bar['close']>bar['open'] else 'bearish_engulfing')
    return dict(tags=tags or ['ordinary'], body_fraction=body/span if span else 0,
                upper_tail_fraction=upper/span if span else 0, lower_tail_fraction=lower/span if span else 0,
                close_location=(bar['close']-bar['low'])/span if span else .5)


class Interactions:
    """Separate identity-based encounters, breaks, retests and role changes."""
    def __init__(self):
        self.tracks = {}

    def observe(self, bar, previous, levels, proximity, sequence):
        incoming = {str(l.get('unified_level_id',l.get('level_id'))): l for l in levels}
        # Retain break witnesses after the source book removes or flips a band.
        expired = [key for key,t in self.tracks.items() if key not in incoming and
                   (t['phase']=='active' or sequence-t['last_seen']>1800)]
        for key in expired:
            del self.tracks[key]
        events = []
        for key, level in incoming.items():
            if key not in self.tracks:
                self.tracks[key] = dict(level=level_evidence(level), phase='active', encounters=0,
                    contact=False, contact_bars=0, break_at=None, last_seen=sequence)
            elif self.tracks[key]['phase']=='active':
                self.tracks[key]['level'] = level_evidence(level)
            self.tracks[key]['last_seen'] = sequence
        if len(self.tracks)>4096:
            raise ValueError('Structural interaction capacity exceeded; refusing partial evidence')
        for key,t in self.tracks.items():
            level = t['level']; sign = direction(level)
            prior_break = t['break_at']
            lower, upper = sorted((sign*level['lower'], sign*level['upper']))
            close, opened = sign*bar['close'], sign*bar['open']
            low, high = sorted((sign*bar['low'], sign*bar['high']))
            prev = sign*previous if previous is not None else None
            eps = max(abs(lower),abs(upper))*1e-12
            contact = high>=lower-eps and low<=upper+eps
            if contact:
                if not t['contact']:
                    t['encounters'] += 1
                t['contact_bars'] += 1
            else:
                t['contact_bars'] = 0
            kind = None
            if t['phase']=='active':
                if prev is not None and prev<=upper+eps and close>upper+eps:
                    kind = 'breakout' if sign==1 else 'support_failure'
                    t.update(phase='retest_pending',break_at=bar['end'])
                elif contact and close<lower-eps and close<opened:
                    kind = 'rejection' if sign==1 else 'support_rejection'
                elif contact:
                    kind = 'testing_resistance' if sign==1 else 'testing_support'
                elif 0<lower-close<=proximity:
                    kind = 'approaching_resistance' if sign==1 else 'approaching_support'
            else:
                # This branch cannot execute on the breaking candle.
                if close<lower-eps:
                    kind = 'failed_breakout' if sign==1 else 'failed_breakdown'
                    if t['phase']=='role_reversed':
                        kind = 'support_reclaim' if sign==-1 else 'resistance_reclaim'
                    t.update(phase='active',break_at=None)
                elif contact:
                    if close>upper+eps:
                        kind = 'support_retest_held' if sign==1 else 'resistance_retest_held'
                        t['phase']='role_reversed'
                    else:
                        kind = 'support_retest_unresolved' if sign==1 else 'resistance_retest_unresolved'
                elif t['phase']=='retest_pending':
                    kind = 'support_retest_pending' if sign==1 else 'resistance_retest_pending'
            t['contact'] = contact
            if kind:
                events.append(dict(state=kind,level=deepcopy(level),phase=t['phase'],
                    encounters=t['encounters'],contact_bars=t['contact_bars'],break_at=t['break_at'] or prior_break))
        return events, len(expired)


def swing_bias(levels):
    """Require both highs and lows to agree; never infer trend from MACD."""
    def pivot_time(level):
        # V5 names the source pivot created_at_ms; local swings use pivot_at.
        return level.get('pivot_at') if level.get('pivot_at') is not None else (
            level['created_at_ms']/1000 if level.get('created_at_ms') is not None else None)
    known = [l for l in levels if pivot_time(l) is not None]
    highs = sorted((l for l in known if direction(l)==1),key=pivot_time)[-2:]
    lows = sorted((l for l in known if direction(l)==-1),key=pivot_time)[-2:]
    if len(highs)<2 or len(lows)<2:
        return 'unknown'
    if any(pivot_time(pair[0])==pivot_time(pair[1]) for pair in (highs,lows)):
        return 'unknown'
    changes = [pair[1]['price']-pair[0]['price'] for pair in (highs,lows)]
    return 'bullish' if all(c>0 for c in changes) else 'bearish' if all(c<0 for c in changes) else 'neutral'
