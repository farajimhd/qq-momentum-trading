"""Causal structural opportunities and hypothetical position management.

All prices are references, never fills. Prior-bar protection is tested before
close-confirmed changes. Importance scores are transparent heuristics, not odds.
"""
from copy import deepcopy
from .structural_labels import level_context, enclosing_levels


def known(level):
    return level.get('confirmed_at_ms')/1000 if level.get('confirmed_at_ms') is not None else level.get('confirmed_at')


def side(level):
    return 1 if level.get('side') in (1,'support') else -1


def key(level):
    return f"{side(level)}:{level['lower']:.12g}:{level['upper']:.12g}"


def overlaps(a,b):
    return all(k in l for l in (a,b) for k in ('lower','upper')) and a['lower']<=b['upper'] and b['lower']<=a['upper']


def level_map(levels, previous, atr, at, reactions, merge_atr):
    visible={(key(l),l.get('scale'),l.get('unified_level_id',l.get('level_id')),known(l)):l
             for l in levels if known(l) is not None and known(l)<=at}
    values=list(visible.values());enclosing=enclosing_levels(values,previous)
    zones=[]
    for l in sorted(visible.values(),key=lambda l:(side(l),l['lower'],l['upper'])):
        context=level_context(l,values,previous,atr,enclosing)
        prior=reactions.get(key(l),{})
        grade=0 if context['significance']=='minor' else 3 if context['significance']=='major' else 2 if context['location']=='outer' else 1
        reactions_count=prior.get('rejections',0)
        if reactions_count>=2: grade=max(grade,2)
        evidence=dict(level=deepcopy(l),context=context,prior_rejections=reactions_count,prior_acceptances=prior.get('acceptances',0))
        if zones and zones[-1]['side']==side(l) and l['lower']-zones[-1]['upper']<=merge_atr*atr:
            z=zones[-1];z['upper']=max(z['upper'],l['upper']);z['importance']=max(z['importance'],grade);z['members'].append(evidence)
        else:
            zones.append(dict(lower=l['lower'],upper=l['upper'],side=side(l),importance=grade,members=[evidence]))
    return zones


def momentum_support(row,sign):
    m=row['momentum'];macd=m['macd'];rsi=m.get('rsi',{})
    if m['agreement']=='warming_up': return False
    trend=macd.get('trend','mixed');watch=macd.get('watch')
    aligned=m['agreement']==('bullish' if sign==1 else 'bearish')
    improving=watch==('watch_up' if sign==1 else 'watch_down') and rsi.get('trend')==('rising' if sign==1 else 'falling')
    weakening=trend==('falling' if sign==1 else 'rising')
    return (aligned or improving) and not weakening


def observe(state,row,levels,settings):
    bar=row['candle'];close=bar['close'];at=row['effective_at'];seq=row['sequence']
    interrupted=state.get('setup') if row['gap_before'] else None
    if row['gap_before']: state.clear()
    previous=state.get('last_bar',bar);atr=row['qualification'].get('atr')
    events=row['global_events']+row['local_events']
    reactions=state.setdefault('reactions',{})
    used=state.setdefault('used',{})
    for memory in (reactions,):
        for k,v in list(memory.items()):
            if seq-(v['sequence'] if isinstance(v,dict) else v)>settings.signal_setup_candles: del memory[k]
    zones=level_map(levels,previous['close'],atr,bar['time'],reactions,settings.signal_zone_atr) if atr else []
    position=interrupted or state.get('setup');action='wait';reason='await_fresh_setup';management=[];evaluations=[]
    def barrier(sign,price,exclude=None):
        candidates=[z for z in zones if z['side']==-sign and z['importance']>=2 and
                    sign*((z['upper'] if sign==1 else z['lower'])-price)>0 and (exclude is None or not overlaps(z,exclude))]
        return min(candidates,key=lambda z:sign*((z['lower'] if sign==1 else z['upper'])-price),default=None)
    def boundary(z,sign): return z['lower'] if sign==1 else z['upper']
    def protective(sign):
        return max((z for z in zones if z['side']==sign and sign*(close-(z['upper'] if sign==1 else z['lower']))>0),
                   key=lambda z:sign*(z['upper'] if sign==1 else z['lower']),default=None)
    if interrupted:
        action=position['direction']+'_exit';reason='context_gap_position_invalidated'
        management.append('exit_reference_only; gap execution price unknown')
    elif position:
        sign=1 if position['direction']=='long' else -1;direction=position['direction'];unit=position['atr']
        stop_hit=bar['low']<=position['stop'] if sign==1 else bar['high']>=position['stop']
        old_barrier=position.get('barrier');old_target=position.get('target')
        target_hit=old_target is not None and (bar['high']>=old_target if sign==1 else bar['low']<=old_target)
        opposite=('support_failure','failed_breakout','resistance_reclaim') if sign==1 else ('breakout','failed_breakdown','support_reclaim')
        opposing=[e for e in events if e['state'] in opposite and (overlaps(e['level'],position['origin']) or any(z['importance']>=2 and overlaps(z,e['level']) for z in zones))]
        supported=momentum_support(row,sign)
        against=sign*(close-previous['close']) < -settings.penetration_atr*unit
        position['weak_closes']=position.get('weak_closes',0)+1 if not supported and against else 0
        excursion=sign*((bar['high'] if sign==1 else bar['low'])-position['entry_reference'])
        adverse=sign*(position['entry_reference']-(bar['low'] if sign==1 else bar['high']))
        position['mfe']=max(position.get('mfe',0),excursion);position['mae']=max(position.get('mae',0),adverse)
        if sign*(close-position.get('best_close',position['entry_reference']))>=settings.penetration_atr*unit:
            position.update(best_close=close,last_progress_sequence=seq)
        giveback=sign*(position.get('best_close',position['entry_reference'])-close)
        rejection=old_barrier and any(overlaps(e['level'],old_barrier) and e['state'] in (('rejection','failed_breakout') if sign==1 else ('support_rejection','failed_breakdown')) for e in events)
        thesis_failed=any(overlaps(e['level'],position['origin']) for e in opposing)
        reversal=any(o['direction']==('bearish' if sign==1 else 'bullish') and o['outcome']=='structural_reversal_confirmation' for o in row.get('volume_analysis',{}).get('reversal_outcomes',[]))
        if stop_hit: reason='both_boundaries_touched_order_unknown' if target_hit else 'stop_reference_touched'
        elif thesis_failed: reason='entry_structure_failed'
        elif not supported and (opposing or reversal): reason='opposing_structure_and_momentum'
        elif rejection and (not supported or against): reason='important_barrier_rejection'
        elif position['weak_closes']>=settings.signal_confirmation_candles and (giveback>=.5*unit or 'repeated_failed_recovery' in row.get('progression',{}).get('tags',[])): reason='failed_progress_and_momentum'
        elif seq-position['last_progress_sequence']>=settings.signal_progress_candles and not supported: reason='no_progress_with_weak_momentum'
        elif seq-position['entry_sequence']>=settings.signal_hold_candles: reason='holding_time_limit'
        else: reason='structure_valid'
        if reason!='structure_valid':
            action=direction+'_exit';state['setup']=None;state['last_exit_sequence']=seq
        else:
            action=direction+'_hold'
            # Targets are decision barriers, not automatic take-profit fills.
            accepted=old_barrier and any(overlaps(e['level'],old_barrier) and e['state']==('breakout_accepted' if sign==1 else 'breakdown_accepted') for e in events)
            passed=old_barrier and sign*(close-(old_barrier['upper'] if sign==1 else old_barrier['lower']))>settings.penetration_atr*unit
            if accepted and passed:
                position['barrier']=barrier(sign,close,old_barrier)
                position['target']=boundary(position['barrier'],sign) if position['barrier'] else None
                management.append('accepted_barrier_advance')
            elif old_barrier is None:
                fresh=barrier(sign,close)
                if fresh: position.update(barrier=fresh,target=boundary(fresh,sign));management.append('new_barrier_observed')
            else:
                fresh=barrier(sign,close)
                if fresh and sign*(boundary(fresh,sign)-old_target)<0:
                    position.update(barrier=fresh,target=boundary(fresh,sign));management.append('nearer_barrier_observed')
            # Confirmed support/resistance can tighten protection only for later bars.
            protective_levels=[l for l in levels+row.get('confirmed_swings',[]) if known(l) is not None and position['entry_at']<known(l)<=at and side(l)==sign
                               and level_context(l,levels,previous['close'],atr or unit)['significance']!='minor']
            protective_levels += [e['level'] for e in events if e['state']==('support_retest_held' if sign==1 else 'resistance_retest_held')]
            stops=[(l['lower']-settings.signal_stop_atr*unit if sign==1 else l['upper']+settings.signal_stop_atr*unit) for l in protective_levels]
            # A thin nearby band must not place protection inside ordinary noise.
            noise_stop=close-sign*settings.signal_min_stop_atr*(atr or unit)
            stops=[min(p,noise_stop) if sign==1 else max(p,noise_stop) for p in stops]
            valid=[p for p in stops if sign*(p-position['stop'])>0 and sign*(close-p)>settings.penetration_atr*unit]
            if valid:
                position['stop']=max(valid) if sign==1 else min(valid);position['stop_effective_at']=at
                management.append('confirmed_structure_stop_tightened')
            if target_hit: management.append('barrier_touched_monitor_acceptance_or_rejection')
        risk=sign*(close-position['stop']);room=sign*(position['target']-close) if position.get('target') is not None else None
        position.update(remaining_room_atr=room/unit if room is not None else None,remaining_reward_risk=room/risk if room is not None and risk>0 else None)
    elif row['gap_before']: reason='context_reset'
    elif not row['qualification']['ready'] or row['momentum']['agreement']=='warming_up': reason='warming_up'
    else:
        candidates=[]
        for sign in (1,-1):
            direction='long' if sign==1 else 'short'
            if not momentum_support(row,sign):
                evaluations.append(dict(direction=direction,rejected='momentum_not_supportive'));continue
            triggers=[]
            kinds={'breakout' if sign==1 else 'support_failure':'initiation',
                   'support_retest_held' if sign==1 else 'resistance_retest_held':'continuation',
                   'support_reclaim' if sign==1 else 'resistance_reclaim':'reversal',
                   'failed_breakdown' if sign==1 else 'failed_breakout':'reversal'}
            for e in events:
                if e['state'] in kinds:
                    token=f"{kinds[e['state']]}:{key(e['level'])}:{e.get('break_at',known(e['level']))}:{e.get('encounters',0)}"
                    if token not in used: triggers.append((kinds[e['state']],e['level'],token))
            anchor=protective(sign)
            displacement=sign*(close-bar['open'])/atr
            fresh_high=sign*(close-(previous['high'] if sign==1 else previous['low']))>settings.penetration_atr*atr
            if anchor and fresh_high and displacement>=settings.break_body_atr:
                tags=row.get('progression',{}).get('tags',[])
                pattern='continuation' if 'recovery_completed' in tags else 'initiation'
                triggers.append((pattern,anchor,f"{pattern}:displacement:{seq}:{sign}"))
            for o in row.get('volume_analysis',{}).get('reversal_outcomes',[]):
                if anchor and o['direction']==('bullish' if sign==1 else 'bearish') and o['outcome']=='structural_reversal_confirmation':
                    triggers.append(('reversal',anchor,f"reversal:{o.get('observed_at')}:{sign}"))
            for pattern,origin,token in triggers:
                if known(origin) is not None and known(origin)>bar['time']: continue
                visible=[m['level'] for z in zones for m in z['members']]
                context=level_context(origin,visible,previous['close'],atr) if 'members' not in origin else None
                grade=origin.get('importance') if context is None else 0 if context['significance']=='minor' else 3 if context['significance']=='major' else 2 if context['location']=='outer' else 1
                stop=origin['lower']-settings.signal_stop_atr*atr if sign==1 else origin['upper']+settings.signal_stop_atr*atr
                noise_stop=close-sign*settings.signal_min_stop_atr*atr
                stop=min(stop,noise_stop) if sign==1 else max(stop,noise_stop)
                risk=sign*(close-stop);extension=sign*(close-(origin['upper'] if sign==1 else origin['lower']))
                # Crossing one member does not clear the rest of its merged zone.
                target_zone=barrier(sign,close);target=boundary(target_zone,sign) if target_zone else None
                room=sign*(target-close) if target is not None else None
                rejected='minor_origin' if grade==0 else 'positive_volume_unavailable' if not bar.get('volume') else 'invalid_or_excessive_risk' if risk<=0 or risk>settings.signal_max_risk_atr*atr else 'entry_extended_from_structure' if extension>settings.signal_max_extension_atr*atr else 'insufficient_structural_room' if room is not None and (room<settings.signal_min_room_atr*atr or room<settings.signal_min_reward_risk*risk) else 'unrated_open_room' if room is None and grade<2 else None
                evaluations.append(dict(direction=direction,pattern=pattern,rejected=rejected,importance=grade,risk_atr=risk/atr,room_atr=room/atr if room is not None else None))
                if rejected: continue
                candidates.append(dict(direction=direction,pattern=pattern,origin=deepcopy(origin),importance=grade,token=token,
                    stop=stop,initial_stop=stop,target=target,initial_target=target,barrier=deepcopy(target_zone),atr=atr,
                    entry_reference=close,entry_at=at,entry_sequence=seq,armed_at=at,last_progress_sequence=seq,best_close=close,
                    reward_risk=room/risk if room is not None else None,room_basis='known_barrier' if target is not None else 'open_room_unbounded_not_forecast',mfe=0.,mae=0.))
        if candidates:
            candidates.sort(key=lambda c:(-c['importance'],{'reversal':0,'continuation':1,'initiation':2}[c['pattern']],abs(close-c['stop']),c['token']))
            if len({c['direction'] for c in candidates})>1: reason='conflicting_directional_setups'
            else:
                position=candidates[0];state['setup']=position;used[position['token']]=seq
                action=position['direction']+'_enter';reason=position['pattern']+'_structure_room_momentum'
        elif evaluations: reason=next((e['rejected'] for e in evaluations if e.get('pattern') and e['rejected']),'await_fresh_setup')
    # Update reaction history only after the current decision has been computed.
    for e in events:
        if e['state'] not in ('rejection','support_rejection','breakout_accepted','breakdown_accepted'): continue
        k=key(e['level']);old=reactions.setdefault(k,dict(rejections=0,acceptances=0))
        witness=(e['state'],e.get('encounters',0),e.get('break_at'))
        if old.get('witness')!=witness:
            field='acceptances' if e['state'].endswith('_accepted') else 'rejections';old[field]+=1
        old.update(witness=witness,sequence=seq)
    for memory in (reactions,used):
        if len(memory)>4096: raise ValueError('Signal evidence capacity exceeded')
    state['last_bar']=deepcopy(bar)
    phase='active' if state.get('setup') else 'idle'
    result=dict(contract='structural-technical-signal-2',phase=phase,action=action,reason=reason,effective_at=at,
        changed=action!=state.get('last_action'),setup=deepcopy(position),management=management,evaluations=evaluations,
        level_map=sorted(zones,key=lambda z:abs((z['lower']+z['upper'])/2-close))[:12],level_map_total=len(zones),level_map_scope='nearest_12_zones; all zones evaluated',execution_eligibility='not_assessed',position_basis='hypothetical_signal_only',
        reference_basis='completed candles; new protection applies after close; no fills inferred')
    state['last_action']=action
    return result
