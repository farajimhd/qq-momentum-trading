"""Closed-candle volume and observed-session extremes; no order-flow inference.

Scores are bounded heuristic evidence strength, never fitted probabilities.
The input may start mid-session: observed extrema are not certified full-day HOD.
"""
from copy import deepcopy
from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


class VolumeLevels:
    def __init__(self, settings):
        self.settings = settings
        self.session = None
        self.baseline = None
        self.samples = 0
        self.previous = None
        self.high = self.low = None
        self.ranks = {'high': [], 'low': []}
        self.legs = {}
        self.leg = None
        self.candidates = {}
        self.sequence = 0

    def observe(self, bar, confirmed, threshold, baseline, state, events):
        day = datetime.fromtimestamp(bar['time'], NY).date().isoformat()
        if day != self.session:
            self.__init__(self.settings)
            self.session = day
            self.context_start = bar['time']
        self.sequence += 1
        prior_high, prior_low = deepcopy(self.high), deepcopy(self.low)
        for side in ('high', 'low'):
            for pivot in confirmed:
                if pivot['side'] != ('resistance' if side == 'high' else 'support'):
                    continue
                if datetime.fromtimestamp(pivot['pivot_at'], NY).date().isoformat() != day:
                    continue
                values = self.ranks[side]
                if not any(abs(pivot['price']-p['price']) <= threshold for p in values):
                    values.append(dict(price=pivot['price'], pivot_at=pivot['pivot_at'], known_at=bar['end']))
                    values.sort(key=lambda p: p['price'], reverse=side == 'high')
                    del values[self.settings.session_level_count:]
        for side, sign in [('high', 1), ('low', -1)]:
            old = getattr(self, side)
            if old is None or sign*(bar[side]-old['price']) > 0:
                setattr(self, side, dict(price=bar[side], at=bar['end']))
        proximity = max(threshold, baseline*self.settings.proximity_body_multiple)
        near = []
        for side, value in [('high', prior_high), ('low', prior_low)]:
            if value and bar['low']-proximity <= value['price'] <= bar['high']+proximity:
                sign = 1 if side=='high' else -1
                crossed = sign*(bar['close']-value['price']) > threshold
                touched = sign*(bar[side]-value['price']) >= 0
                rejected = touched and sign*(bar['close']-value['price']) < -threshold
                interaction = 'cross' if crossed else 'rejection' if rejected else 'test' if touched else 'approach'
                near.append(dict(side=side, rank=1, kind='running_extreme', interaction=interaction, **value))
            for rank, pivot in enumerate(self.ranks[side], 1):
                if bar['low']-proximity <= pivot['price'] <= bar['high']+proximity:
                    near.append(dict(side=side, rank=rank, kind='confirmed_swing', **pivot))
        levels = dict(session=day, timezone='America/New_York', scope='loaded_session_candles',
            context_start=self.context_start, full_session_verified=False,
            high=deepcopy(self.high), low=deepcopy(self.low), prior_high=prior_high, prior_low=prior_low,
            ranked_highs=deepcopy(self.ranks['high']), ranked_lows=deepcopy(self.ranks['low']), near=near)
        intraday = datetime.fromtimestamp(bar['end']-.000001, NY).date().isoformat() == day
        levels['status'] = 'partial_session' if intraday else 'unavailable_multisession_candle'
        if not intraday:
            near = []
            levels['near'] = []
        volume = bar.get('volume')
        if volume is not None and (not isfinite(volume) or volume < 0):
            raise ValueError('Volume must be finite and nonnegative, or unavailable')
        duration = bar['end']-bar['time']
        rate = volume/duration if volume is not None else None
        color = 'green' if bar['close'] > bar['open'] else 'red' if bar['close'] < bar['open'] else 'neutral'
        prior = self.previous
        delta = bar['close']-prior['close'] if prior else 0
        sign = 1 if delta > threshold else -1 if delta < -threshold else 0
        # Comparable directional legs use volume/time, not a sum biased by length.
        if sign and self.leg and self.leg['direction'] != sign:
            self.legs[self.leg['direction']] = deepcopy(self.leg)
            self.leg = None
        if sign and self.leg is None:
            self.leg = dict(direction=sign, started_at=bar['end'], volume=0., duration=0., complete=True)
        if self.leg:
            self.leg['duration'] += duration
            if volume is None:
                self.leg['complete'] = False
            else:
                self.leg['volume'] += volume
        prior_rate = prior['rate'] if prior else None
        ratio = rate/prior_rate if rate is not None and prior_rate is not None and prior_rate > 0 else None
        relative = rate/self.baseline if rate is not None and self.baseline and self.baseline > 0 else None
        tags = []
        tags.extend(('observed_hod_' if n['side']=='high' else 'observed_lod_')+n['interaction'] for n in near if n['kind']=='running_extreme')
        if ratio is not None:
            tags.append('volume_falling' if ratio < 1-self.settings.volume_change_fraction else
                        'volume_rising' if ratio > 1+self.settings.volume_change_fraction else 'volume_stable')
        if prior and prior['color'] != color:
            tags.append(prior['color']+'_to_'+color)
        if state in ('pullback', 'upward_retracement') and 'volume_falling' in tags:
            tags.append('countermove_volume_fading')
        recovery_sign = 1 if prior and prior['state']=='pullback' else -1 if prior and prior['state']=='upward_retracement' else 0
        if recovery_sign and sign == recovery_sign and state in ('recovery', 'advance', 'downward_recovery', 'decline'):
            tags.append('recovery_after_countermove')
            if near:
                tags.append('recovery_at_session_level')
        comparisons = []
        if ratio is not None:
            comparisons.append(dict(reference='previous_candle', ratio=ratio))
        old_leg = self.legs.get(sign)
        if self.leg and self.leg['complete'] and old_leg and old_leg['complete'] and old_leg['volume'] > 0:
            comparisons.append(dict(reference='previous_same_direction_leg',
                ratio=(self.leg['volume']/self.leg['duration'])/(old_leg['volume']/old_leg['duration'])))
        # Do not cherry-pick the more favorable reference when the previous
        # candle and comparable directional leg disagree.
        contraction = min([max(0, 1-c['ratio']) for c in comparisons] or [0.])
        supported = rate is not None and rate > 0 and self.samples >= self.settings.volume_warmup_candles
        direction = 'bearish' if sign == 1 else 'bullish' if sign == -1 else 'none'
        side = 'high' if sign == 1 else 'low'
        near_extreme = any(n['side'] == side for n in near)
        relevant = ('rejection', 'resistance_forming', 'failed_breakout') if sign == 1 else ('support_rejection', 'support_forming', 'failed_breakdown')
        structural = any(e['state'] in relevant for e in events) or any(n['side']==side and n.get('interaction')=='rejection' for n in near)
        components = dict(volume_contraction=contraction, price_progress=min(1, abs(delta)/max(baseline, threshold)),
                          extreme_context=float(near_extreme), structural_rejection=float(structural))
        # Contraction is mandatory. Context strengthens evidence but cannot create it.
        score = round(100*contraction*(components['price_progress']+components['extreme_context']+components['structural_rejection'])/3, 1) if supported and sign else None
        divergence = dict(direction=direction if score and contraction >= self.settings.volume_change_fraction else 'none',
            score=score, score_kind='heuristic_evidence_not_probability', components=components,
            comparisons=comparisons, status='available' if supported else 'unavailable' if rate is None else 'zero_volume' if rate == 0 else 'warming_up',
            interpretation='price_volume_divergence' if score and contraction >= self.settings.volume_change_fraction else 'no_qualified_divergence',
            reversal_confirmed=False)
        if supported:
            if sign and ratio is not None and ratio > 1+self.settings.volume_change_fraction:
                tags.append('price_volume_expansion')
            if relative is not None and relative >= self.settings.volume_expansion_multiple:
                if abs(delta) <= threshold:
                    tags.append('high_effort_low_progress')
                if any(e['state'] in ('rejection','support_rejection','failed_breakout','failed_breakdown') for e in events):
                    tags.append('high_volume_rejection')
                if any(e['state'] in ('breakout','support_failure') for e in events):
                    tags.append('high_volume_break')
        outcomes = []
        for candidate_direction, candidate in list(self.candidates.items()):
            confirmation_events = ('support_failure','failed_breakout','resistance_reclaim') if candidate_direction == 'bearish' else ('breakout','failed_breakdown','support_reclaim')
            crossed = bar['close'] < candidate['confirmation_price']-threshold if candidate_direction == 'bearish' else bar['close'] > candidate['confirmation_price']+threshold
            expired = self.sequence-candidate['sequence'] > self.settings.volume_setup_max_candles
            confirmed = not expired and crossed and any(e['state'] in confirmation_events for e in events)
            continuing = bar['close'] > candidate['extreme']+threshold if candidate_direction=='bearish' else bar['close'] < candidate['extreme']-threshold
            invalidated = continuing and 'price_volume_expansion' in tags
            if confirmed or expired or invalidated:
                outcomes.append(dict(candidate, outcome='structural_reversal_confirmation' if confirmed else 'expired_unconfirmed' if expired else 'invalidated_by_volume_supported_continuation', effective_at=bar['end']))
                del self.candidates[candidate_direction]
        if divergence['direction'] != 'none' and score >= self.settings.volume_divergence_min_score and (near_extreme or structural):
            if direction not in self.candidates:
                self.candidates[direction] = dict(direction=direction, score=score, observed_at=bar['end'], sequence=self.sequence,
                    extreme=bar['high'] if direction=='bearish' else bar['low'],
                    confirmation_price=bar['low'] if direction == 'bearish' else bar['high'])
            else:
                self.candidates[direction].update(score=score,updated_at=bar['end'],
                    extreme=bar['high'] if direction=='bearish' else bar['low'],
                    confirmation_price=bar['low'] if direction=='bearish' else bar['high'])
            tags.append(direction+'_exhaustion_candidate')
        tags.extend(o['direction']+'_reversal_confirmation' for o in outcomes if o['outcome']=='structural_reversal_confirmation')
        result = dict(status='unavailable' if rate is None else 'available', volume=volume, rate=rate,
            color=color, color_basis='close_vs_open_not_buy_sell_flow', previous_ratio=ratio, relative_volume=relative,
            baseline_rate=self.baseline, prior_samples=self.samples, tags=tags, divergence=divergence,
            reversal_candidates=deepcopy(list(self.candidates.values())), reversal_outcomes=outcomes)
        if rate is not None:
            alpha = 1-2**(-1/self.settings.volume_half_life)
            self.baseline = rate if self.baseline is None else self.baseline+alpha*(rate-self.baseline)
            self.samples += 1
        self.previous = dict(close=bar['close'], rate=rate, color=color, state=state)
        return result, levels
