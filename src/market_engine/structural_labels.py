"""Causal qualification and display taxonomy; never a probability or trade gate."""
from copy import deepcopy


def enclosing_levels(levels, previous):
    supports = [l for l in levels if l.get('side') in (1, 'support') and l['upper'] <= previous]
    resistances = [l for l in levels if l.get('side') in (-1, 'resistance') and l['lower'] >= previous]
    return (max(supports, key=lambda l: l['upper'], default=None),
            min(resistances, key=lambda l: l['lower'], default=None))


def level_context(level, levels, previous, atr, enclosing=None):
    """Location is relative to the enclosing *prior* range, not book origin.

    Outer means a boundary of the nearest enclosing support/resistance pair.
    Missing enclosing sides leave location unknown. Source scale is independent.
    """
    support, resistance = enclosing or enclosing_levels(levels,previous)
    location = 'unknown'
    bounds = None
    if support and resistance:
        bounds = [support['lower'], resistance['upper']]
        location = 'outer' if any(level['lower']==l['lower'] and level['upper']==l['upper'] for l in (support,resistance)) else 'internal' if bounds[0]<=level['lower']<=level['upper']<=bounds[1] else 'outside'
    # V6 prominence/selection_score are dimensionless evidence scores. Only
    # reversal_distance is a price distance comparable with ATR.
    prominence = level.get('reversal_distance')
    prominence_atr = prominence / atr if isinstance(prominence, (float,int)) and atr else None
    score = level.get('selection_score')
    minimum = level.get('selection_minimum_score')
    below_selection = isinstance(score,(int,float)) and isinstance(minimum,(int,float)) and score<minimum
    return dict(location=location, enclosing_range=bounds, scale=level.get('scale','unknown'),
                prominence_atr=prominence_atr,
                selection_score=level.get('selection_score',level.get('prominence')),
                significance='minor' if below_selection or prominence_atr is not None and prominence_atr < 1 else
                    'major' if level.get('scale')=='major' or prominence_atr is not None and prominence_atr>=2 else 'unrated')


def label_packet(row, prior_signature, recent, atr):
    """All families are retained; one deterministic changed/high-priority summary."""
    families = dict(movement=[row['state']], regime=[], geometry=row['candle_shape']['tags'],
        displacement=[], interaction=[], break_lifecycle=[], retest_lifecycle=[],
        structural_progression=[], correction_recovery=[], pressure=[], volume=row['volume_analysis']['tags'],
        reversal=[], evidence=[])
    bar = row['candle']
    body = abs(bar['close']-bar['open'])
    ready = row['qualification']['ready']
    families['displacement'] = ['unavailable' if not ready else 'small_body' if body<.3*atr else
                                'large_body' if body>=atr else 'ordinary_body']
    if len(recent)>=5 and ready:
        window = list(recent)[-5:]+[bar]
        path = sum(abs(b['close']-a['close']) for a,b in zip(window,window[1:]))
        efficiency = abs(window[-1]['close']-window[0]['close'])/path if path else 0
        width = max(b['high'] for b in window)-min(b['low'] for b in window)
        families['regime'] = ['compression' if width<=2*atr else 'directional' if efficiency>=.65 else 'rotation']
    else:
        families['regime'] = ['warming_up']
    candidates = []
    for scope in ('local','global'):
        for event in row[scope+'_events']:
            kind = event['state']
            family = 'retest_lifecycle' if 'retest' in kind else 'break_lifecycle' if any(s in kind for s in ('break','failure','cross','reclaim')) else 'structural_progression' if kind.endswith(('_confirmed','_forming')) else 'interaction'
            families[family].append(scope+':'+kind)
            important = any(s in kind for s in ('failed_break','reclaim','accepted','retest_held'))
            priority = 90 if important else 80 if kind in ('breakout','support_failure') else 65 if 'rejection' in kind else 50 if 'confirmed' in kind else 35 if 'cross' in kind else 20
            if event.get('context',{}).get('significance')=='minor':
                priority -= 20
            short = {'breakout':'Break ↑','support_failure':'Break ↓','resistance_cross':'Cross ↑',
                'support_cross':'Cross ↓','rejection':'Reject ↓','support_rejection':'Reject ↑',
                'support_retest_held':'Retest ↑','resistance_retest_held':'Retest ↓',
                'breakout_accepted':'Accept ↑','breakdown_accepted':'Accept ↓',
                'failed_breakout':'Fail ↓','failed_breakdown':'Fail ↑',
                'swing_high_confirmed':'Swing high','swing_low_confirmed':'Swing low',
                'lower_high_confirmed':'Lower high','higher_low_confirmed':'Higher low',
                'equal_high_confirmed':'Equal high','equal_low_confirmed':'Equal low',
                'resistance_forming':'High forming','support_forming':'Low forming',
                'testing_resistance':'Test R','testing_support':'Test S',
                'approaching_resistance':'Near R','approaching_support':'Near S',
                'support_retest_pending':'Retest S…','resistance_retest_pending':'Retest R…',
                'support_retest_unresolved':'Test S?','resistance_retest_unresolved':'Test R?',
                'support_reclaim':'Reclaim ↑','resistance_reclaim':'Reclaim ↓'}.get(kind,kind.replace('_',' '))
            candidates.append(dict(family=family,label=kind,text=short,priority=priority,scope=scope,
                level=deepcopy(event['level']),context=deepcopy(event.get('context'))))
    for tag in row['progression']['tags']:
        family = 'pressure' if 'pressure' in tag else 'correction_recovery' if any(s in tag for s in ('recovery','pullback','correction','cycle')) else 'structural_progression'
        families[family].append(tag)
    families['reversal'] = [c['direction']+'_candidate' for c in row['volume_analysis']['reversal_candidates']]
    families['reversal'] += [o['direction']+'_'+o['outcome'] for o in row['volume_analysis']['reversal_outcomes']]
    notable = {
        'recovery_completed':('Recovered',70), 'deep_correction':('Deep pullback',60),
        'repeated_failed_recovery':('Recovery fails',75), 'losing_gained_levels':('Levels lost',85),
        'persistent_resistance_pressure':('Pressure R',70), 'persistent_support_pressure':('Pressure S',70),
        'high_effort_low_progress':('Effort > move',55), 'high_volume_rejection':('Volume reject',70),
        'bullish_structural_reversal_confirmation':('Reversal ↑',95),
        'bearish_structural_reversal_confirmation':('Reversal ↓',95)}
    for family, tags in families.items():
        for tag in tags:
            if tag in notable:
                short, priority = notable[tag]
                candidates.append(dict(family=family,label=tag,text=short,priority=priority))
    families['evidence'] = ['atr_ready' if ready else 'atr_warming_up',
                           'global_available' if row['global_status']=='available' else 'global_unavailable']
    if row['gap_before']:
        families['evidence'].append('context_reset')
    candidates.append(dict(family='movement',label=row['state'],text=dict(advance='Advance ↑',decline='Decline ↓',
        pullback='Pullback ↓',upward_retracement='Retrace ↑',recovery='Recover ↑',downward_recovery='Recover ↓',
        no_change='Quiet',unknown='Warmup').get(row['state'],row['state']),priority=10))
    summary = max(candidates,key=lambda c:(c['priority'],c.get('scope')=='global'))
    chosen = summary.get('level',{})
    signature = (summary['family'],summary['label'],summary.get('scope'),chosen.get('lower'),chosen.get('upper'),chosen.get('price'))
    summary['changed'] = signature != prior_signature
    return families, summary, signature
