"""Causal V5 resistance breakout policy; no ticker or session-specific rules."""
from math import ceil, floor, isfinite

from . import swing_gap

CONTRACT = 'swing-v5-breakout-1'
DEFAULTS = dict(direction_window_ms=400., maximum_sample_gap_ms=250.,
                breakout_lifetime_ms=1000., stop_offset_bps=5., target_offset_ticks=1,
                minimum_selection_score=30., entry_resistance_count=3)


def enabled(parameters):
    return parameters.get('v5_breakout_contract') == CONTRACT


def configure(parameters):
    if parameters['v5_breakout_contract'] != CONTRACT:
        raise ValueError('Unknown V5 breakout contract')
    if not parameters.get('swing_evidence_contract'):
        raise ValueError('V5 breakout requires MACD entry rules')
    if parameters.get('swing_gap_contract') or parameters.get('swing_momentum_contract'):
        raise ValueError('V5 breakout must not inherit another structural policy')
    settings = dict(DEFAULTS, **parameters.get('v5_breakout', {}))
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in settings.values()):
        raise ValueError('V5 breakout settings must be finite and positive')
    if not 30 <= settings['minimum_selection_score'] <= 100:
        raise ValueError('V5 grade must be between 30 and 100')
    for key in ('entry_resistance_count', 'target_offset_ticks'):
        if type(settings[key]) is not int:
            raise ValueError(key+' must be an integer')
    parameters['v5_breakout'] = settings
    parameters.update(completed_macd_setup=False, require_completed_entry_candle=False,
                      require_breakout_reset=False)
    parameters['entry_body_breakout'] = dict(enabled=True, offset_ticks=1)
    parameters['entry_candle_confirmation'].update(enabled=False, require_closed_bar=False,
        evaluate_macd_intrabar=True, reject_bearish_close=False)
    parameters['structural_entry'].update(enabled=False, accept_live_price_above_entry_level=True)
    parameters['market_pressure']['enabled'] = False
    parameters['protection']['stop'].update(method='structure', cap_initial_stop_distance=False)
    parameters['protection']['trailing'].update(enabled=False, mode='qualified_support')
    parameters['reentry'].update(after_protective_exit=True, require_new_confirmation=False, cooldown_ms=0)
    parameters['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=False)
    parameters['momentum_management']['macd_backstop']['enabled'] = False


def rows(observation, parameters):
    return sorted((r for r in swing_gap.levels(observation, {'minimum_p_norm': 0})
                   if r['book_version'] == 'causal-swing-closing-book-5' and r['side'] == -1
                   and r['selection_score'] >= parameters['v5_breakout']['minimum_selection_score']),
                  key=lambda r: (r['upper'], r['lower'], str(r['unified_level_id'])))


def below(level, parameters):
    tick = parameters['execution']['tick_size']
    offset = max(tick, level['lower'] * parameters['v5_breakout']['stop_offset_bps']/10000)
    return floor((level['lower']-offset)/tick+1e-9)*tick


def target(levels, broken, average, parameters):
    reference = broken['upper'] + average
    above = [r for r in levels if r['lower'] > reference]
    if len(above) < 2:
        return None
    row = above[1]
    tick = parameters['execution']['tick_size']
    price = (ceil(row['lower']/tick-1e-9)-parameters['v5_breakout']['target_offset_ticks'])*tick
    return dict(price=price, level=row, average_body=average, reference=reference)


def observe(observation, parameters, state):
    now, price = observation.observed_at.timestamp(), observation.price
    policy = parameters['v5_breakout']
    data = dict(state.get('v5_breakout_state') or {})
    previous = data.get('sample')
    if previous and now < previous[0]:
        return
    levels = rows(observation, parameters)
    crossed = []
    if 'market_data_update' in observation.evaluation_events and isfinite(price) and price > 0:
        history = [r for r in data.get('history', []) if now-2 <= r[0] < now]
        history.append((now, price))
        data['history'] = history[-1000:]
        if previous and 0 < now-previous[0] <= policy['maximum_sample_gap_ms']/1000:
            # Only boundaries already known before this trade can be broken.
            crossed = [r for r in data.get('levels', []) if previous[1] <= r['upper'] < price]
        data['sample'] = (now, price)
    data['levels'] = levels
    data['crossed'] = crossed
    if observation.position_quantity <= 0:
        data.pop('move', None)
        data.pop('pending_target', None)
        high = observation.structural_session_high
        vwap = observation.execution_vwap
        ranked = sorted((r for r in data.get('prior_levels', levels)
                         if high and r['upper'] <= high), key=lambda r:r['upper'], reverse=True)
        ids = {r['unified_level_id'] for r in ranked[:policy['entry_resistance_count']]}
        eligible = [r for r in crossed if r['unified_level_id'] in ids and vwap and r['lower'] > vwap]
        if eligible:
            data['breakout'] = dict(level=eligible[-1], at=now)
        breakout = data.get('breakout')
        if breakout and (price <= breakout['level']['upper'] or now-breakout['at'] > policy['breakout_lifetime_ms']/1000):
            data.pop('breakout', None)
    data['prior_levels'] = levels
    move = dict(data.get('move') or {})
    if move and observation.source_timeframe == '1s' and 'bar_close' in observation.evaluation_events:
        # Exclude the partial candle that contains the resistance break.
        if now-1 >= move['started'] and now > move.get('last_bar', 0):
            opening = observation.bar_open
            if opening and isfinite(opening):
                move['sum'] += abs(price-opening)
                move['count'] += 1
                move['last_bar'] = now
        data['move'] = move
    state['v5_breakout_state'] = data


def select(observation, parameters, state):
    data = state.get('v5_breakout_state') or {}
    reference = state.get('entry_body_reference') or {}
    now, price = observation.observed_at.timestamp(), observation.price
    result = dict(reason='v5_body_reference_unavailable')
    if not reference or not reference['end'] <= now < reference['expires'] or 'market_data_update' not in observation.evaluation_events:
        return result
    threshold = max(reference['open'], reference['close'])
    state['entry_body_trigger'] = dict(reference, threshold=threshold, price=price)
    if price <= threshold:
        return dict(reason='v5_previous_body_not_broken')
    policy = parameters['v5_breakout']
    history = data.get('history', [])
    start = next((i for i in range(len(history)-1, -1, -1)
                  if history[i][0] <= now-policy['direction_window_ms']/1000), None)
    if start is None:
        return dict(reason='v5_direction_warming')
    window = history[start:]
    if (price <= window[0][1] or any(b[0]-a[0] > policy['maximum_sample_gap_ms']/1000
                                   for a,b in zip(window, window[1:]))):
        return dict(reason='v5_direction_not_upward')
    vwap = observation.execution_vwap
    if not vwap or price <= vwap:
        return dict(reason='v5_price_not_above_vwap')
    breakout = data.get('breakout')
    if not breakout:
        return dict(reason='v5_waiting_for_resistance_break')
    broken = breakout['level']
    if broken['lower'] <= vwap:
        return dict(reason='v5_resistance_not_above_vwap')
    stop = below(broken, parameters)
    if not 0 < stop < min(price, observation.bid):
        return dict(reason='v5_stop_already_triggered')
    selected = target(data['levels'], broken, 0., parameters)
    if not selected or selected['price'] <= max(price, observation.ask):
        return dict(reason='v5_second_target_unavailable')
    return dict(reason='', stop=stop, target=selected['price'], target_selection=selected,
                broken=broken, broken_at=breakout['at'], direction_reference=window[0])


def manage(observation, parameters, state):
    data = state['v5_breakout_state']
    current = float(state.get('active_stop') or state.get('initial_stop') or 0)
    selection = state.get('v5_entry_selection')
    if not selection:
        return current
    move = data.get('move')
    if not move:
        move = dict(started=selection['broken_at'], sum=0., count=0,
                    broken=selection['broken'], seen={r['unified_level_id'] for r in data['levels']})
    # Keep assignment state JSON serializable.
    seen = set(move['seen'])
    crossed = [r for r in data['crossed'] if r['upper'] > move['broken']['upper']]
    if crossed:
        broken = max(crossed, key=lambda r:r['upper'])
        average = move['sum']/move['count'] if move['count'] else 0.
        selected = target(data['levels'], broken, average, parameters)
        if selected and selected['price'] > observation.price:
            data['pending_target'] = selected
        current = max(current, below(broken, parameters))
        move.update(started=observation.observed_at.timestamp(), sum=0., count=0, broken=broken)
    for row in data['levels']:
        if (row['unified_level_id'] not in seen and row['confirmed_at_ms']/1000 > selection['broken_at']
                and row['lower'] <= float(state.get('high_water_price') or observation.price)):
            current = max(current, below(row, parameters))
    move['seen'] = [r['unified_level_id'] for r in data['levels']]
    data['move'] = move
    return current
