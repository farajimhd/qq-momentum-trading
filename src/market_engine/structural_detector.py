"""Causal candle regimes. No strategy, position, order, or MACD eligibility gates.

Consumers supply completed OHLC and the global book known at that close. A
decision is a value snapshot; later swing confirmations never mutate it.
"""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, asdict
from math import isfinite

from .swing_structure import SwingSettings, SwingStructure

VERSION = 'structural-candle-detector-1'


@dataclass(frozen=True)
class DetectorSettings:
    reversal_bps: float = 50
    volatility_multiple: float = 2
    body_half_life: float = 5
    consolidation_body_multiple: float = .25
    proximity_body_multiple: float = 1
    macd_gap_bps: float = 25

    def __post_init__(self):
        if any(not isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('Detector settings must be finite and positive')


def compact(level):
    return {k: level[k] for k in ('unified_level_id', 'level_id', 'side', 'lower', 'upper',
            'price', 'pivot_at', 'confirmed_at', 'confirmed_at_ms', 'book_version') if k in level}


def interaction(bar, previous, levels, proximity):
    """Compare against levels available before this candle, including broken ones."""
    events = []
    for level in levels:
        lower, upper = level['lower'], level['upper']
        epsilon = max(abs(lower), abs(upper))*1e-12
        resistance = level['side'] in (-1, 'resistance')
        if resistance:
            if previous is not None and previous <= upper+epsilon and bar['close'] > upper+epsilon:
                kind = 'breakout'
            elif bar['high'] >= lower and bar['close'] < lower and bar['close'] < bar['open']:
                kind = 'rejection'
            elif bar['high'] >= lower and bar['low'] <= upper:
                kind = 'testing_resistance'
            elif 0 < lower-bar['close'] <= proximity:
                kind = 'approaching_resistance'
            else:
                continue
        else:
            if previous is not None and previous >= lower-epsilon and bar['close'] < lower-epsilon:
                kind = 'support_failure'
            elif bar['low'] <= upper and bar['close'] >= lower:
                kind = 'testing_support'
            else:
                continue
        events.append(dict(state=kind, level=compact(level)))
    return events


class StructuralDetector:
    def __init__(self, settings=DetectorSettings()):
        self.settings = settings
        self.swings = SwingStructure(SwingSettings(reversal_bps=settings.reversal_bps,
            volatility_multiple=settings.volatility_multiple))
        self.last = None
        self.body = None
        self.high = None
        self.pullback = None
        self.global_levels = []
        self.broken = deque(maxlen=32)
        self.sequence = 0
        self.last_support = None
        self.advanced = False
        self.ema_fast = self.ema_slow = self.signal = None
        self.episode = None
        self.close_times = {}

    def local_evidence(self, level):
        result = compact(level)
        for key in ('pivot_at', 'confirmed_at'):
            if result.get(key) is not None:
                result[key] = self.close_times[result[key]]
        return result

    def observe(self, bar, levels=None, global_status='unavailable'):
        start, end = bar['time'], bar['end']
        if not all(isfinite(bar[k]) for k in ('time', 'end', 'open', 'high', 'low', 'close')):
            raise ValueError('Non-finite candle')
        if end <= start or bar['low'] <= 0 or not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            raise ValueError('Invalid completed OHLC candle')
        if self.last and (end <= self.last['end'] or start < self.last['end']):
            raise ValueError('Candles must be distinct, ordered, and non-overlapping')
        previous = self.last['close'] if self.last else None
        baseline = self.body or max(abs(bar['close']-bar['open']), bar['close']*self.settings.reversal_bps/10000)
        local_before = [self.local_evidence(l) for l in self.swings.active.values() if l['scale']=='local' and l['state']=='active']
        local_events = interaction(bar, previous, local_before, baseline*self.settings.proximity_body_multiple)
        # Use the previous as-of snapshot for crossings. A level can disappear
        # or change side in the current snapshot precisely because it broke.
        available = {str(l.get('unified_level_id', l.get('level_id'))): l for l in self.global_levels}
        for level in levels or []:
            known = level.get('confirmed_at_ms', end*1000)/1000
            if known > end:
                raise ValueError('Future global swing evidence')
            available.setdefault(str(level.get('unified_level_id', level.get('level_id'))), level)
        global_events = interaction(bar, previous, list(available.values()), baseline*self.settings.proximity_body_multiple) if global_status=='available' else []
        for event in global_events:
            if event['state']=='breakout':
                self.broken.append(dict(event['level'], broken_at=end))
        held = [l for l in self.broken if bar['close'] > l['upper']]
        # The shared local extractor's temporal constants are interpreted in
        # candles, not wall seconds. This keeps 100ms/minute/daily charts causal
        # and prevents every daily level from expiring on the next daily bar.
        local_time = self.sequence+1
        self.close_times[local_time] = end
        self.swings.observe(local_time, bar['high'], bar['low'], bar['close'])
        # The structure engine's visual archive is not this indicator's history.
        # Retain bounded active state; immutable decisions are owned by caller.
        self.swings.segments.clear()
        for level in self.swings.active.values():
            level.pop('segment', None)
        new_local = [self.local_evidence(l) for l in self.swings.active.values() if l['scale']=='local' and l['confirmed_at']==local_time]
        for level in new_local:
            kind = 'resistance_confirmed' if level['side']=='resistance' else ('higher_low_confirmed' if self.last_support is not None and level['price']>self.last_support else 'swing_low_confirmed')
            local_events.append(dict(state=kind, level=level))
            if level['side']=='support':
                self.last_support = level['price']
        forming = self.swings.detectors[0]
        high = forming.get('high')
        retreat = previous is not None and bar['close'] < previous
        # A developing ceiling needs a previously observed extreme and a
        # meaningful retreat. The current candle cannot confirm its own wick.
        ceiling = high and high[1] < local_time and high[0]-bar['close'] >= baseline and retreat
        if ceiling:
            local_events.append(dict(state='resistance_forming', level=dict(price=high[0], pivot_at=self.close_times[high[1]], confirmed_at=None)))
        support_failed = any(e['state']=='support_failure' for e in local_events)
        if previous is None:
            state, reason = 'unknown', 'insufficient_history'
        elif support_failed:
            state, reason = 'decline', 'local_support_failed'
        elif retreat:
            state, reason = ('pullback', 'retreat_with_local_support_intact') if self.advanced else ('decline', 'lower_close_without_prior_advance')
            if self.pullback is None:
                self.pullback = dict(high=max(self.high or previous, previous), low=bar['low'])
            else:
                self.pullback['low'] = min(self.pullback['low'], bar['low'])
        elif self.pullback and bar['close'] <= self.pullback['high']:
            if bar['close'] > previous:
                self.pullback['recovering'] = True
            state, reason = ('recovery', 'recovering_below_prior_high') if self.pullback.get('recovering') else ('pullback', 'pullback_not_yet_recovering')
        elif abs(bar['close']-previous) <= baseline*self.settings.consolidation_body_multiple and abs(bar['close']-bar['open']) <= baseline*self.settings.consolidation_body_multiple:
            state, reason = 'consolidation', 'small_body_and_close_change'
        else:
            state, reason = 'advance', 'prior_high_reclaimed' if self.pullback else 'rising_close'
            self.pullback = None
            self.advanced = True
        if support_failed:
            self.advanced = False
            self.high = bar['close']
            self.pullback = None
        self.high = max(self.high or bar['close'], bar['open'], bar['close'])
        alpha = 1-2**(-1/self.settings.body_half_life)
        self.body = abs(bar['close']-bar['open']) if self.body is None else self.body+alpha*(abs(bar['close']-bar['open'])-self.body)
        self.ema_fast = bar['close'] if self.ema_fast is None else self.ema_fast+2/13*(bar['close']-self.ema_fast)
        self.ema_slow = bar['close'] if self.ema_slow is None else self.ema_slow+2/27*(bar['close']-self.ema_slow)
        macd = self.ema_fast-self.ema_slow
        self.signal = macd if self.signal is None else self.signal+2/10*(macd-self.signal)
        self.sequence += 1
        histogram_bps = (macd-self.signal)/bar['close']*10000
        active = self.sequence>=26 and histogram_bps>=self.settings.macd_gap_bps
        if not active:
            self.episode = None
        elif self.episode is None:
            self.episode = end
        result = dict(contract=VERSION, sequence=self.sequence, time=start, effective_at=end,
            state=state, reason=reason, local_events=local_events, global_events=global_events,
            global_context=('above_broken_resistance' if held else 'between_levels') if global_status=='available' else 'unavailable',
            global_status=global_status, local_swings=local_before, confirmed_swings=new_local,
            broken_resistance=compact(max(held, key=lambda l:l['upper'])) if held else None,
            body_baseline=baseline, pullback=deepcopy(self.pullback),
            developing_swings={side:dict(price=forming[side][0], pivot_at=self.close_times[forming[side][1]], confirmed=False)
                for side in ('high', 'low') if forming.get(side)},
            macd=dict(histogram_bps=histogram_bps, warmup=self.sequence<26, active=active, episode_started_at=self.episode),
            candle=dict(bar), gap_before=bool(self.last and start>self.last['end']))
        self.last = dict(bar)
        self.global_levels = deepcopy(levels or []) if global_status=='available' else []
        referenced = {local_time}
        for level in self.swings.active.values():
            referenced.update((level['pivot_at'], level['confirmed_at']))
        for detector in self.swings.detectors:
            referenced.update(detector[side][1] for side in ('high','low') if detector.get(side))
        self.close_times = {k:v for k,v in self.close_times.items() if k in referenced}
        return result
