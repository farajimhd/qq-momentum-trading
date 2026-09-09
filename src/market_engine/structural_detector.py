"""Causal candle regimes. No strategy, position, order, or MACD eligibility gates.

Consumers supply completed OHLC and the global book known at that close. A
decision is a value snapshot; later swing confirmations never mutate it.
"""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, asdict
from math import isfinite

from .swing_structure import SwingSettings, SwingStructure
from .structural_evidence import Interactions, morphology, swing_bias

VERSION = 'structural-candle-detector-2'


@dataclass(frozen=True)
class DetectorSettings:
    reversal_bps: float = 50
    volatility_multiple: float = 2
    body_half_life: float = 5
    consolidation_body_multiple: float = .25
    proximity_body_multiple: float = 1
    macd_gap_bps: float = 25
    tail_range_fraction: float = .5
    indecision_body_fraction: float = .2
    expansion_body_multiple: float = 1.5
    expansion_body_fraction: float = .65

    def __post_init__(self):
        if any(not isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('Detector settings must be finite and positive')


def compact(level):
    return {k: level[k] for k in ('unified_level_id', 'level_id', 'side', 'lower', 'upper',
            'price', 'pivot_at', 'confirmed_at', 'confirmed_at_ms', 'book_version') if k in level}



class StructuralDetector:
    def __init__(self, settings=DetectorSettings()):
        self.settings = settings
        self.swings = SwingStructure(SwingSettings(reversal_bps=settings.reversal_bps,
            volatility_multiple=settings.volatility_multiple))
        self.last = None
        self.body = None
        self.pullback = None
        self.global_levels = []
        self.sequence = 0
        self.last_support = None
        self.last_resistance = None
        self.trend = 0
        self.extreme = None
        self.local_interactions = Interactions()
        self.global_interactions = Interactions()
        self.pivots = deque(maxlen=64)
        self.episode_direction = 0
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
        local_events, local_expired = self.local_interactions.observe(bar, previous, local_before, baseline*self.settings.proximity_body_multiple, self.sequence)
        # Use the previous as-of snapshot for crossings. A level can disappear
        # or change side in the current snapshot precisely because it broke.
        available = {str(l.get('unified_level_id', l.get('level_id'))): l for l in self.global_levels}
        for level in levels or []:
            known = level.get('confirmed_at_ms', end*1000)/1000
            if known > end:
                raise ValueError('Future global swing evidence')
            available.setdefault(str(level.get('unified_level_id', level.get('level_id'))), level)
        global_events, global_expired = self.global_interactions.observe(bar, previous, list(available.values()), baseline*self.settings.proximity_body_multiple, self.sequence) if global_status=='available' else ([], 0)
        held = [t['level'] for t in self.global_interactions.tracks.values() if t['phase']!='active' and t['level']['side'] in (-1,'resistance') and bar['close']>t['level']['upper']]
        below = [t['level'] for t in self.global_interactions.tracks.values() if t['phase']!='active' and t['level']['side'] in (1,'support') and bar['close']<t['level']['lower']]
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
            if level['side']=='resistance':
                kind = 'lower_high_confirmed' if self.last_resistance is not None and level['price']<self.last_resistance else 'swing_high_confirmed'
                self.last_resistance = level['price']
            else:
                kind = 'higher_low_confirmed' if self.last_support is not None and level['price']>self.last_support else 'swing_low_confirmed'
            local_events.append(dict(state=kind, level=level))
            if level['side']=='support':
                self.last_support = level['price']
        self.pivots.extend(new_local)
        forming = self.swings.detectors[0]
        for side, sign, name in [('high',1,'resistance_forming'),('low',-1,'support_forming')]:
            extreme = forming.get(side)
            if extreme and extreme[1]<local_time and sign*(extreme[0]-bar['close'])>=baseline and previous is not None and sign*(bar['close']-previous)<0:
                local_events.append(dict(state=name,level=dict(price=extreme[0],pivot_at=self.close_times[extreme[1]],confirmed_at=None)))
        local_bias = swing_bias(list(self.pivots))
        global_bias = swing_bias(list(available.values())) if global_status=='available' else 'unknown'
        delta = bar['close']-previous if previous is not None else 0
        broken_up = any(e['state'] in ('breakout','support_reclaim') for e in local_events)
        broken_down = any(e['state'] in ('support_failure','resistance_reclaim') for e in local_events)
        if broken_up != broken_down:
            self.trend = 1 if broken_up else -1
            self.extreme = bar['close']
            self.pullback = None
        elif new_local and local_bias in ('bullish','bearish'):
            structural_trend = 1 if local_bias=='bullish' else -1
            if structural_trend != self.trend:
                self.trend = structural_trend
                self.extreme = bar['close']
                self.pullback = None
        if previous is None:
            state, reason = 'unknown','insufficient_history'
        else:
            if self.trend==0 and delta!=0:
                self.trend = 1 if delta>0 else -1
                self.extreme = previous
            sign = self.trend or 1
            if sign*delta<0:
                state = 'pullback' if sign==1 else 'upward_retracement'
                reason = 'countertrend_close_without_confirmed_local_break'
                if self.pullback is None:
                    self.pullback = dict(reference=self.extreme, direction=sign)
            elif abs(delta)<=baseline*self.settings.consolidation_body_multiple and abs(bar['close']-bar['open'])<=baseline*self.settings.consolidation_body_multiple and not self.pullback:
                state,reason = 'consolidation','small_body_and_close_change'
            elif self.pullback and sign*(bar['close']-self.pullback['reference'])<=0:
                state = 'recovery' if sign==1 else 'downward_recovery'
                reason = 'recovering_toward_prior_extreme'
            else:
                state = 'advance' if sign==1 else 'decline'
                reason = 'prior_extreme_reclaimed' if self.pullback else 'directional_close'
                self.pullback = None
            if self.extreme is None or sign*(bar['close']-self.extreme)>0:
                self.extreme = bar['close']
        shape = morphology(bar,self.last,baseline,self.settings)
        alpha = 1-2**(-1/self.settings.body_half_life)
        self.body = abs(bar['close']-bar['open']) if self.body is None else self.body+alpha*(abs(bar['close']-bar['open'])-self.body)
        self.ema_fast = bar['close'] if self.ema_fast is None else self.ema_fast+2/13*(bar['close']-self.ema_fast)
        self.ema_slow = bar['close'] if self.ema_slow is None else self.ema_slow+2/27*(bar['close']-self.ema_slow)
        macd = self.ema_fast-self.ema_slow
        self.signal = macd if self.signal is None else self.signal+2/10*(macd-self.signal)
        self.sequence += 1
        histogram_bps = (macd-self.signal)/bar['close']*10000
        episode_direction = (1 if histogram_bps>=self.settings.macd_gap_bps else -1 if histogram_bps<=-self.settings.macd_gap_bps else 0) if self.sequence>=26 else 0
        active = episode_direction!=0
        if not active:
            self.episode = None
        elif self.episode is None or episode_direction!=self.episode_direction:
            self.episode = end
        self.episode_direction = episode_direction
        result = dict(contract=VERSION, sequence=self.sequence, time=start, effective_at=end,
            state=state, reason=reason, direction=('bullish' if self.trend==1 else 'bearish' if self.trend==-1 else 'unknown'),
            local_bias=local_bias, global_bias=global_bias, candle_shape=shape,
            expired_interactions=dict(local=local_expired,global_count=global_expired), local_events=local_events, global_events=global_events,
            global_context=('mixed_breaks' if held and below else 'above_broken_resistance' if held else 'below_broken_support' if below else 'between_levels') if global_status=='available' else 'unavailable',
            global_status=global_status, local_swings=local_before, confirmed_swings=new_local,
            broken_resistance=compact(max(held, key=lambda l:l['upper'])) if held else None,
            broken_support=compact(min(below,key=lambda l:l['lower'])) if below else None,
            body_baseline=baseline, pullback=deepcopy(self.pullback),
            developing_swings={side:dict(price=forming[side][0], pivot_at=self.close_times[forming[side][1]], confirmed=False)
                for side in ('high', 'low') if forming.get(side)},
            macd=dict(histogram_bps=histogram_bps, warmup=self.sequence<26, active=active, direction=episode_direction, episode_started_at=self.episode),
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
