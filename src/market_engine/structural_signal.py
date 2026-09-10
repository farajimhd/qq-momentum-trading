"""Causal technical setup lifecycle. References are not fills or broker orders."""
from copy import deepcopy


def observe(state, row, levels, settings):
    bar=row['candle']; close=bar['close']; now=row['effective_at']; seq=row['sequence']
    events=row['global_events']+row['local_events']
    momentum=row['momentum']; agreement=momentum['agreement']
    action='wait'; reason='await_qualified_break'; previous_phase=state.get('phase','idle')
    setup=state.get('setup'); phase=previous_phase
    def same(event):
        level=event['level']
        return level.get('lower')==setup['lower'] and level.get('upper')==setup['upper']
    if row['gap_before']:
        state.clear();setup=None;phase='idle';reason='context_reset'
    elif setup:
        direction=setup['direction'];long=direction=='long';sign=1 if long else -1
        matching=[e['state'] for e in events if same(e)]
        invalid=('failed_breakout','resistance_reclaim') if long else ('failed_breakdown','support_reclaim')
        if phase=='active':
            stop_hit=bar['low']<=setup['stop'] if long else bar['high']>=setup['stop']
            target_hit=bar['high']>=setup['target'] if long else bar['low']<=setup['target']
            if stop_hit or target_hit or any(e in invalid for e in matching):
                action=direction+'_exit';phase='idle'
                reason='both_boundaries_touched_order_unknown' if stop_hit and target_hit else 'stop_reference_touched' if stop_hit else 'target_reference_touched' if target_hit else 'structural_invalidation'
            elif seq-setup['entry_sequence']>=settings.signal_hold_candles:
                action=direction+'_exit';phase='idle';reason='holding_time_limit'
            else:
                action=direction+'_hold';reason='structure_valid'
        elif seq-setup['sequence']>settings.signal_setup_candles:
            action='cancel_setup';phase='idle';reason='setup_expired'
        elif any(e in invalid for e in matching) or sign*(close-setup['stop'])<=0:
            action='cancel_setup';phase='idle';reason='setup_invalidated'
        else:
            accepted='breakout_accepted' if long else 'breakdown_accepted'
            held='support_retest_held' if long else 'resistance_retest_held'
            if accepted in matching: setup['accepted_at']=now;phase='accepted'
            if held in matching:
                # A held retest itself establishes acceptance in the detector.
                setup.setdefault('accepted_at',now);setup['retest_at']=now;phase='confirmed'
            reason='await_acceptance' if phase=='armed' else 'await_retest'
            if phase=='confirmed':
                risk=sign*(close-setup['stop']);reward=sign*(setup['target']-close)
                if 'retest_sequence' in setup and seq-setup['retest_sequence']>settings.signal_confirmation_candles:
                    action='cancel_setup';phase='idle';reason='confirmation_expired'
                elif agreement!=('bullish' if long else 'bearish'):
                    reason='await_supportive_momentum'
                elif bar.get('volume') is None or bar['volume']<=0:
                    reason='positive_volume_unavailable'
                elif risk<=0 or reward<settings.signal_min_reward_risk*risk:
                    reason='insufficient_remaining_reward'
                elif sign*(close-(setup['upper'] if long else setup['lower']))<=0:
                    reason='price_inside_level'
                else:
                    phase='active';action=direction+'_enter';reason='break_acceptance_retest_momentum'
                    setup.update(entry_reference=close,entry_at=now,entry_sequence=seq,reward_risk=reward/risk)
            if held in matching: setup.setdefault('retest_sequence',seq)
    elif reason!='context_reset' and row['qualification']['ready'] and agreement!='warming_up':
        candidates=[]
        for event in events:
            if event['state'] not in ('breakout','support_failure'): continue
            if event.get('context',{}).get('significance')=='minor': continue
            level=event['level'];long=event['state']=='breakout';sign=1 if long else -1
            atr=row['qualification']['atr'];stop=(level['lower']-atr*settings.signal_stop_atr) if long else level['upper']+atr*settings.signal_stop_atr
            targets=[]
            for other in levels:
                known=other.get('confirmed_at_ms',0)/1000 if 'confirmed_at_ms' in other else other.get('confirmed_at')
                if known is None or known>bar['time']: continue
                is_resistance=other.get('side') in (-1,'resistance')
                if is_resistance!=long: continue
                target=other.get('lower') if long else other.get('upper')
                if target is not None and sign*(target-close)>0: targets.append(target)
            if not targets: continue
            target=min(targets) if long else max(targets)
            risk=sign*(close-stop);reward=sign*(target-close)
            if risk<=0 or reward<settings.signal_min_reward_risk*risk: continue
            candidates.append(dict(direction='long' if long else 'short',lower=level['lower'],upper=level['upper'],
                level=deepcopy(level),band_id=event.get('band_id'),sequence=seq,armed_at=now,atr=atr,stop=stop,target=target))
        if len({c['direction'] for c in candidates})>1:
            reason='conflicting_directional_setups'
        elif candidates:
            setup=candidates[0];phase='armed';action=setup['direction']+'_armed';reason='qualified_break_with_target'
        elif any(e['state'] in ('breakout','support_failure') for e in events):
            reason='no_eligible_target_or_reward'
        else:
            watch=momentum['macd']['watch']
            action='long_watch' if watch=='watch_up' else 'short_watch' if watch=='watch_down' else 'wait'
            reason='momentum_watch' if action!='wait' else 'await_qualified_break'
    elif reason!='context_reset':
        reason='warming_up'
    if setup and phase in ('armed','accepted','confirmed'):
        action=setup['direction']+'_'+phase
    state.update(phase=phase,setup=setup if phase!='idle' else None)
    result=dict(contract='structural-technical-signal-1',phase=phase,action=action,reason=reason,
        effective_at=now,changed=action!=state.get('last_action') or phase!=previous_phase,
        setup=deepcopy(setup),execution_eligibility='not_assessed',position_basis='hypothetical_signal_only',
        reference_basis='completed_candle; boundary touches observed at close; no fill inference')
    state['last_action']=action
    return result
