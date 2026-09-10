"""Causal movement cycles and structural progress, independent of trade state."""
from collections import deque
from copy import deepcopy

from .structural_evidence import band_key


class Progression:
    def __init__(self, settings):
        self.settings = settings
        self.direction = 0
        self.leg = 0
        self.extreme = None
        self.origin = None
        self.cycle = None
        self.cycles = deque(maxlen=16)
        self.cycle_number = 0
        self.completed_count = 0
        self.gained = {}

    def observe(self, bar, previous, direction, baseline, threshold, local, global_events, local_bias, global_bias, movement_delta=None):
        tags = []
        closed_cycle = None
        previous_losses = {'local':0,'global':0}
        previous_direction = self.direction
        if direction != self.direction:
            if self.direction:
                tags.append('local_direction_changed')
                failures=('failed_breakout','resistance_reclaim') if self.direction==1 else ('failed_breakdown','support_reclaim')
                for scope,events in [('local',local),('global',global_events)]:
                    for event in events:
                        if event['state'] in failures and 'lower' in event['level']:
                            gained=self.gained.get((scope,band_key(event['level'])))
                            if gained and gained['accepted']:
                                previous_losses[scope] += 1
            if self.cycle:
                closed_cycle = dict(self.cycle,outcome='invalidated_by_direction_change',ended_at=bar['end'])
                self.cycles.append(closed_cycle)
            self.direction = direction
            self.leg += 1
            self.origin = previous if previous is not None else bar['open']
            self.extreme = self.origin
            self.cycle = None
            self.gained = {}
            self.completed_count = 0
        sign = direction or 1
        if self.extreme is None:
            self.extreme = bar['close']
            self.origin = bar['open']
        delta = sign*movement_delta if movement_delta is not None else sign*(bar['close']-previous) if previous is not None else 0
        depth = max(0,sign*(self.extreme-bar['close']))
        if direction and not self.cycle and depth>threshold:
            self.cycle_number += 1
            self.cycle = dict(number=self.cycle_number,leg=self.leg,direction=direction,
                started_at=bar['end'],reference=self.extreme,baseline=baseline,depth=depth,
                candles=0,attempts=0,failed_attempts=0,recovering=False,
                attempt_peak=None,previous_attempt_peak=None,recovery_progress=0.)
        if self.cycle:
            c=self.cycle
            c['candles'] += 1
            c['depth'] = max(c['depth'],max(0,sign*(c['reference']-bar['close'])))
            if delta>threshold:
                if not c['recovering']:
                    c['attempts'] += 1
                    c['recovering'] = True
                    c['attempt_peak'] = bar['close']
                elif sign*(bar['close']-c['attempt_peak'])>0:
                    c['attempt_peak'] = bar['close']
                if c['previous_attempt_peak'] is not None:
                    improvement=sign*(c['attempt_peak']-c['previous_attempt_peak'])
                    tags.append('improving_recovery' if improvement>threshold else 'recovery_below_previous_attempt' if improvement < -threshold else 'similar_recovery')
            elif delta < -threshold and c['recovering']:
                c['recovering'] = False
                c['failed_attempts'] += 1
                c['previous_attempt_peak'] = c['attempt_peak']
            remaining=max(0,sign*(c['reference']-bar['close']))
            c['recovery_progress']=max(0,min(1,1-remaining/c['depth'])) if c['depth'] else 0
            c['duration_seconds']=bar['end']-c['started_at']
            if sign*(bar['close']-c['reference'])>threshold:
                prior=next((v for v in reversed(self.cycles) if v['leg']==self.leg and v['outcome']=='recovered'),None)
                closed_cycle=dict(c,outcome='recovered',ended_at=bar['end'])
                if prior:
                    if c['depth']<prior['depth']-threshold and c['candles']<=prior['candles']:
                        tags.append('improving_cycles')
                    elif c['depth']>prior['depth']+threshold and c['candles']>=prior['candles']:
                        tags.append('deteriorating_cycles')
                    else:
                        tags.append('mixed_cycle_progress')
                self.cycles.append(closed_cycle)
                self.completed_count += 1
                self.cycle=None
                tags.append('recovery_completed')
            else:
                tags.append('recovery_attempt' if c['recovering'] else 'pullback_in_progress')
                if c['failed_attempts']>=2:
                    tags.append('repeated_failed_recovery')
                if c['depth']>=c['baseline']*self.settings.deep_correction_multiple:
                    tags.append('deep_correction')
        if sign*(bar['close']-self.extreme)>0:
            self.extreme=bar['close']
        crossed = {'local':0,'global':0}
        lost = previous_losses
        pressure = []
        for scope,events in [('local',local),('global',global_events)]:
            for event in events:
                kind=event['state']
                level=event['level']
                if 'lower' not in level:
                    continue
                key=(scope,band_key(level))
                if kind==('breakout' if sign==1 else 'support_failure'):
                    crossed[scope] += 1
                    self.gained[key]=dict(level=deepcopy(level),accepted=event.get('qualification',{}).get('accepted',True),broken_at=bar['end'])
                acceptance = ('breakout_accepted','support_retest_held') if sign==1 else ('breakdown_accepted','resistance_retest_held')
                if kind in acceptance:
                    if key in self.gained:
                        self.gained[key]['accepted']=True
                if kind in (('failed_breakout','resistance_reclaim') if sign==1 else ('failed_breakdown','support_reclaim')):
                    if key in self.gained and self.gained[key]['accepted']:
                        self.gained[key]['accepted']=False
                        lost[scope] += 1
                if kind in ('rejection','support_rejection','testing_resistance','testing_support') and (
                    event.get('rejection_closes',0)>=self.settings.pressure_closes or
                    event.get('encounters',0)>=2 and event.get('rejection_trend')!='unestablished'):
                    pressure.append(dict(scope=scope,level=deepcopy(level),encounters=event['encounters'],
                        rejection_closes=event['rejection_closes'],trend=event['rejection_trend']))
                    tags.append('persistent_resistance_pressure' if level['side'] in (-1,'resistance') else 'persistent_support_pressure')
        if sum(crossed.values()):
            tags.append('levels_crossed')
        if any(n>1 for n in crossed.values()):
            tags.append('multiple_levels_crossed')
        if sum(lost.values()):
            tags.append('losing_gained_levels')
        if len(self.gained)>4096:
            raise ValueError('Progression level capacity exceeded; refusing partial evidence')
        context = 'local_'+local_bias+'_global_'+global_bias
        if {local_bias,global_bias}=={'bullish','bearish'}:
            tags.append('local_global_conflict')
        return dict(tags=list(dict.fromkeys(tags)) or ['no_new_progress'],leg=self.leg,
            cycle=deepcopy(self.cycle),closed_cycle=deepcopy(closed_cycle),
            completed_cycles=self.completed_count,
            crossed=crossed,lost=lost,accepted_levels=sum(v['accepted'] for v in self.gained.values()),
            accepted_by_scope={scope:sum(v['accepted'] for k,v in self.gained.items() if k[0]==scope) for scope in ('local','global')},
            previous_direction=previous_direction,
            pressure=pressure,structure_context=context)
