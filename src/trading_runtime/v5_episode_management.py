"""Opt-in episode management with explicit causal attempt evidence."""
from math import isfinite


DEFAULTS = dict(rejection_from_below=True, rejection_closes=1,
                rejection_atr_multiple=0., stop_atr_multiple=0., take_profit_fraction=1.,
                entry_on_close=False, entry_range_seconds=0.,
                profit_trail_atr_multiple=0., profit_trail_activation_atr=1.,
                entry_confirmation_window_ms=0., maximum_macd_line_bps=0.,
                entry_minimum_close_location=0., require_range_context=False,
                rejection_buffer_requires_armed_trail=False,
                position_structure_enabled=False, swing_left_bars=2, swing_right_bars=2,
                swing_buffer_bps=5., swing_buffer_atr_multiple=.25,
                same_episode_reentry_stop=False, reentry_stop_offset_bps=5.,
                adaptive_target_enabled=False, adaptive_target_body_window=8,
                adaptive_target_gap_window=32, adaptive_target_body_multiple=2.,
                adaptive_target_gap_multiple=1., adaptive_target_contraction_ratio=.5,
                adaptive_target_exhaustion_closes=2, adaptive_target_body_half_life=0.,
                expansion_stop_enabled=False, expansion_stop_gap_fraction=.5,
                expansion_stop_minimum_breaks=2, expansion_stop_close_location=.75,
                expansion_stop_atr_multiple=.25, expansion_stop_gap_cap=.25,
                defensive_structure_enabled=False, structural_stop_offset_bps=0.,
                completed_body_reentry_enabled=False,
                continuation_detector_enabled=False, detector_recovery_body_multiple=.5)


def configure(parameters):
    raw = parameters.get('episode_management')
    if raw is None:
        return
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('Unknown episode management setting')
    policy = dict(DEFAULTS, **raw)
    if any(type(policy[key]) is not bool for key in ('rejection_from_below','entry_on_close','require_range_context',
                                                   'rejection_buffer_requires_armed_trail', 'position_structure_enabled',
                                                   'same_episode_reentry_stop', 'adaptive_target_enabled', 'expansion_stop_enabled',
                                                   'defensive_structure_enabled', 'completed_body_reentry_enabled', 'continuation_detector_enabled')):
        raise ValueError('Episode policy flags must be boolean')
    if type(policy['rejection_closes']) is not int or not 1 <= policy['rejection_closes'] <= 60:
        raise ValueError('Rejection confirmation must be one to sixty completed candles')
    for key in ('rejection_atr_multiple', 'stop_atr_multiple', 'entry_range_seconds',
                'profit_trail_atr_multiple', 'profit_trail_activation_atr', 'entry_confirmation_window_ms',
                'maximum_macd_line_bps', 'entry_minimum_close_location',
                'swing_buffer_bps', 'swing_buffer_atr_multiple', 'reentry_stop_offset_bps'):
        if type(policy[key]) not in (int, float) or not isfinite(policy[key]) or policy[key] < 0:
            raise ValueError('ATR multiples must be finite and nonnegative')
    if policy['entry_range_seconds'] > 3600:
        raise ValueError('Recent-range history is bounded to one hour')
    if policy['entry_confirmation_window_ms'] > 1000:
        raise ValueError('Entry confirmation cannot outlive its one-second candle interval')
    if policy['entry_confirmation_window_ms'] and not policy['entry_on_close']:
        raise ValueError('An execution window requires completed-candle entry confirmation')
    if policy['entry_minimum_close_location'] > 1:
        raise ValueError('Close location must be between zero and one')
    if policy['entry_minimum_close_location'] and not policy['entry_on_close']:
        raise ValueError('Close location requires completed-candle entry confirmation')
    if policy['require_range_context'] and not (policy['entry_on_close'] and policy['entry_range_seconds']):
        raise ValueError('Required range context needs a positive range and completed-candle entry')
    if policy['rejection_buffer_requires_armed_trail'] and not (
            policy['rejection_atr_multiple'] > 0 and policy['profit_trail_atr_multiple'] > 0):
        raise ValueError('Earned rejection tolerance requires a positive buffer and profit trail')
    fraction = policy['take_profit_fraction']
    if type(fraction) not in (int, float) or not isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Target fraction must be between zero and one')
    for key in ('swing_left_bars', 'swing_right_bars'):
        if type(policy[key]) is not int or not 1 <= policy[key] <= 60:
            raise ValueError('Swing confirmation must use one to sixty candles per side')
    if policy['reentry_stop_offset_bps'] >= 10000:
        raise ValueError('Re-entry stop offset must be less than 100 percent')
    for key in ('adaptive_target_body_window', 'adaptive_target_gap_window', 'adaptive_target_exhaustion_closes'):
        if type(policy[key]) is not int or not 1 <= policy[key] <= 120:
            raise ValueError('Adaptive target windows must be between one and 120 samples')
    for key in ('adaptive_target_body_multiple', 'adaptive_target_gap_multiple', 'adaptive_target_contraction_ratio', 'detector_recovery_body_multiple'):
        if type(policy[key]) not in (int, float) or not isfinite(policy[key]) or policy[key] <= 0:
            raise ValueError('Adaptive target multipliers must be positive and finite')
    if policy['adaptive_target_contraction_ratio'] > 1:
        raise ValueError('Adaptive contraction ratio cannot exceed one')
    if policy['adaptive_target_enabled'] and not policy['position_structure_enabled']:
        raise ValueError('Adaptive targets require position structure and its full broker target')
    for key in ('adaptive_target_body_half_life', 'expansion_stop_atr_multiple', 'structural_stop_offset_bps'):
        if type(policy[key]) not in (int, float) or not isfinite(policy[key]) or policy[key] < 0:
            raise ValueError('Weighting and expansion buffer must be finite and nonnegative')
    for key in ('expansion_stop_gap_fraction', 'expansion_stop_close_location', 'expansion_stop_gap_cap'):
        if type(policy[key]) not in (int, float) or not isfinite(policy[key]) or not 0 < policy[key] <= 1:
            raise ValueError('Expansion fractions must be in (0, 1]')
    if type(policy['expansion_stop_minimum_breaks']) is not int or not 2 <= policy['expansion_stop_minimum_breaks'] <= 120:
        raise ValueError('Expansion requires two to 120 distinct bands')
    if policy['expansion_stop_enabled'] and not (policy['position_structure_enabled'] and policy['adaptive_target_enabled']):
        raise ValueError('Expansion stops require adaptive targets and position structure')
    if policy['defensive_structure_enabled'] and not policy['expansion_stop_enabled']:
        raise ValueError('Defensive structure requires expansion protection')
    if policy['continuation_detector_enabled'] and not (policy['defensive_structure_enabled'] and policy['completed_body_reentry_enabled']):
        raise ValueError('Continuation detector requires defensive structure and completed-body reentry')
    if policy['completed_body_reentry_enabled'] and (not policy['same_episode_reentry_stop']
            or parameters.get('macd_evaluation_mode') != 'completed_1s'):
        raise ValueError('Completed-body reentry requires episode stops and completed 1s MACD')
    if policy['structural_stop_offset_bps'] >= 10000:
        raise ValueError('Structural offset must be less than 100 percent')
    if policy['position_structure_enabled']:
        from .v5_breakout import MACD_REJECTION_CONTRACT
        if parameters.get('v5_breakout_contract') != MACD_REJECTION_CONTRACT:
            raise ValueError('Position structure requires the V5 MACD episode rejection contract')
        if policy['profit_trail_atr_multiple'] or policy['rejection_buffer_requires_armed_trail']:
            raise ValueError('Position structure cannot use an independent ATR profit trail')
        if fraction != 1. or parameters.get('broken_level_stop_only'):
            raise ValueError('Position structure requires a full-position broker profit target')
    parameters['episode_management'] = policy


def profit_trail(o, d, state, policy, tick, current):
    """Optional close-only profit protection, scoped to one acquired lifecycle."""
    from math import floor
    multiple = policy.get('profit_trail_atr_multiple', 0.)
    if not multiple or o.position_quantity <= 0 or o.average_price <= 0:
        return current
    if not (o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events):
        return current
    now = o.observed_at.timestamp()
    trail = dict(d.get('profit_trail') or {})
    if now <= trail.get('observed_at', 0):
        return max(current, trail.get('stop', current))
    atr = d.get('closed_atr', 0.)
    entry_atr = state.get('v5_entry_selection', {}).get('entry_atr', 0.)
    if atr <= 0 or entry_atr <= 0:
        return current
    peak = max(trail.get('peak_close', o.average_price), o.price)
    armed = trail.get('armed', False) or peak > o.average_price + policy['profit_trail_activation_atr']*entry_atr
    proposed = floor((peak-multiple*atr)/tick+1e-9)*tick if armed else current
    current = max(current, proposed, trail.get('stop', current))
    d['profit_trail'] = dict(observed_at=now, peak_close=peak, armed=armed,
        entry_atr=entry_atr, current_atr=atr, stop=current, confirmed_at=o.observed_at.isoformat())
    return current


def observe_entry(o, d, closed, policy):
    seconds = policy['entry_range_seconds']
    now = o.observed_at.timestamp()
    if not seconds or not closed or now <= d.get('closed_at', 0):
        return
    context = o.completed_range_context
    from .completed_candle_range import CONTRACT
    valid = (policy.get('entry_on_close') and context.get('contract') == CONTRACT and context.get('ready') is True
             and context.get('as_of') == now and context.get('seconds') == seconds)
    d['entry_range_context_valid'] = valid
    if valid:
        d['entry_range_high'] = context['high']
        d['entry_range_samples'] = context['samples']
        d['entry_range_context'] = dict(context)
        d.pop('entry_range_history', None)
        return
    if policy.get('require_range_context'):
        d['entry_range_high'] = None
        d['entry_range_samples'] = 0
        return
    history = [row for row in d.get('entry_range_history', []) if row[0] >= now-seconds]
    history.append([now, max(o.price, o.bar_high or o.price)])
    reference = history[:-1] if policy['entry_on_close'] else history
    d['entry_range_high'] = max((row[1] for row in reference), default=None)
    d['entry_range_samples'] = len(reference)
    d['entry_range_history'] = history


def observe_rejection(o, d, levels, closed, policy):
    from .v5_macd_episode import strictly_below
    previous = d.get('attempt_previous_price')
    d['attempt_previous_price'] = o.price
    if o.position_quantity <= 0:
        d.pop('resistance_contacts', None)
        d.pop('resistance_rejection', None)
        return
    contacts = dict(d.get('resistance_contacts') or {})
    multiple = policy['rejection_atr_multiple']
    trail = d.get('profit_trail') or {}
    if policy.get('rejection_buffer_requires_armed_trail'):
        # Only an already observed completed-close witness can earn tolerance.
        # Freeze that decision when the attempt starts; later profit must not
        # retrospectively widen an existing failure boundary.
        witnessed_at = trail.get('observed_at')
        earned = (trail.get('armed') is True and type(witnessed_at) in (int, float)
                  and isfinite(witnessed_at) and witnessed_at <= o.observed_at.timestamp())
        if not earned or d.get('closed_atr', 0.) <= 0:
            multiple = 0.
    for level in levels:
        in_band = not strictly_below(o.price, level['lower']) and not strictly_below(level['upper'], o.price)
        approached = previous is not None and strictly_below(previous, level['lower'])
        volatility_ready = not multiple or d.get('closed_atr', 0.) > 0
        if in_band and volatility_ready and (approached or not policy['rejection_from_below']):
            contact = dict(
                level=dict(level), touched_at=o.observed_at.isoformat(), touch_price=o.price,
                approach_price=previous, below_closes=0,
                rejection_boundary=level['lower'] - multiple * d.get('closed_atr', 0.))
            if policy.get('rejection_buffer_requires_armed_trail'):
                contact.update(rejection_buffer_multiple=multiple,
                               rejection_buffer_witness=dict(trail) if multiple else None)
            contacts.setdefault(str(level['unified_level_id']), contact)
    if closed and o.observed_at.timestamp() > d.get('closed_at', 0):
        retained = {}
        for key, contact in contacts.items():
            contact = dict(contact)
            level = contact['level']
            if strictly_below(level['upper'], o.price):
                continue
            contact['below_closes'] = contact.get('below_closes', 0)+1 if strictly_below(
                o.price, contact['rejection_boundary']) else 0
            if contact['below_closes'] >= policy['rejection_closes']:
                if not d.get('resistance_rejection'):
                    d['resistance_rejection'] = dict(contact, confirmed_at=o.observed_at.isoformat(),
                        close_price=o.price, timeframe='1s', required_closes=policy['rejection_closes'])
            else:
                retained[key] = contact
        contacts = retained
    d['resistance_contacts'] = contacts
