"""Frozen R1-R4 stages, followed by a causal local-top expansion phase."""
from math import ceil, isfinite
from datetime import datetime
from .v5_breakout import below, target


def manage(observation, parameters, state):
    data = state['v5_breakout_state']
    selection = state.get('v5_entry_selection') or {}
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    refs = selection.get('references', [])
    if len(refs) != 4:
        return current
    now, price = observation.observed_at.timestamp(), observation.price
    started = selection['broken_at']
    if parameters.get('v5_breakout_contract') == 'swing-v5-staged-breakout-2':
        started = datetime.fromisoformat(state['entry_at']).timestamp() if state.get('entry_at') else now
    stage = dict(data.get('stages') or dict(phase='r4', started=started,
        total=0., count=0, last_bar=0., top=None, broken=refs[3]))
    old_top = stage.get('top')
    average = stage['total']/stage['count'] if stage['count'] else None
    closed = observation.source_timeframe == '1s' and 'bar_close' in observation.evaluation_events
    body = abs(price-observation.bar_open) if observation.bar_open and isfinite(observation.bar_open) else None
    complete = closed and body is not None and now-1 >= stage['started'] and now > stage['last_bar']
    if stage['phase'] == 'r4' and price > refs[2]['upper']:
        current = max(current, below(refs[3], parameters))
        stage['phase'] = 'r3'
        above = next((r for r in data['levels'] if r['lower'] > refs[0]['upper']), None)
        if above:
            tick = parameters['execution']['tick_size']
            data['pending_target'] = dict(price=(ceil(above['lower']/tick-1e-9)-parameters['v5_breakout']['target_offset_ticks'])*tick,
                level=above, reference=refs[0]['upper'], average_body=average or 0.)
    if stage['phase'] == 'r3' and price > refs[0]['upper']:
        stage['phase'] = 'waiting_local_top'
        stage['r1_broken_at'] = now
    reset = False
    if (stage['phase'] == 'waiting_local_top' and old_top and complete and average is not None
            and observation.bar_open <= old_top['upper'] < price and body > average):
        stage.update(phase='adaptive', total=body, count=1, last_bar=now,
                     started=now-1, broken=old_top, seen=[r['unified_level_id'] for r in data['levels']])
        current = max(current, below(old_top, parameters))
        selected = target(data['levels'], old_top, body, parameters)
        if selected and selected['price'] > price:
            data['pending_target'] = selected
        reset = True
    elif stage['phase'] == 'adaptive':
        crossed = [r for r in data['crossed'] if r['upper'] > stage['broken']['upper']]
        if crossed:
            broken = max(crossed, key=lambda r:r['upper'])
            current = max(current, below(broken, parameters))
            selected = target(data['levels'], broken, average or 0., parameters)
            if selected and selected['price'] > price:
                data['pending_target'] = selected
            stage['broken'] = broken
        seen = set(stage.get('seen', []))
        for row in data['levels']:
            if (row['unified_level_id'] not in seen and row['confirmed_at_ms']/1000 > stage['started']
                    and row['lower'] <= state.get('high_water_price',price)):
                current = max(current, below(row, parameters))
        stage['seen'] = [r['unified_level_id'] for r in data['levels']]
    if complete and not reset:
        stage.update(total=stage['total']+body, count=stage['count']+1, last_bar=now)
    # Only tops confirmed after R1 broke are local breakout candidates. A newly
    # observed top cannot authorize a crossing by the same completed candle.
    if stage['phase'] == 'waiting_local_top':
        locals_ = [r for r in data['levels'] if r['confirmed_at_ms']/1000 > stage['r1_broken_at']
                   and r['upper'] > refs[0]['upper'] and r['lower'] <= state.get('high_water_price',price)]
        if locals_:
            stage['top'] = max(locals_,key=lambda r:r['upper'])
    data['stages'] = stage
    state['v5_stage_evidence'] = dict(phase=stage['phase'], average_body=stage['total']/stage['count'] if stage['count'] else None,
        count=stage['count'], local_top=stage.get('top'), active_stop=current)
    return current
