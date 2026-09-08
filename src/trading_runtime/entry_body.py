"""Causal one-second body breakout; one completed reference per ticker state."""
from math import isfinite


def enabled(parameters):
    return bool(parameters.get('entry_body_breakout', {}).get('enabled'))


def configure(parameters):
    if not enabled(parameters):
        return
    ticks = parameters['entry_body_breakout'].get('offset_ticks', 1)
    if type(ticks) is not int or ticks < 1:
        raise ValueError('Entry body breakout offset must be a positive number of ticks')
    parameters['entry_body_breakout']['offset_ticks'] = ticks
    parameters['require_completed_entry_candle'] = False
    parameters['entry_candle_confirmation'].update(enabled=False, require_closed_bar=False,
        evaluate_macd_intrabar=True, reject_bearish_close=False)
    parameters['structural_entry']['accept_live_price_above_entry_level'] = True


def observe(observation, state):
    if observation.source_timeframe != '1s' or 'bar_close' not in observation.evaluation_events:
        return
    end = observation.observed_at.timestamp()
    old = state.get('entry_body_reference') or {}
    if end <= old.get('end', float('-inf')):
        return
    opening, close = observation.bar_open, observation.price
    if not all(isinstance(v, (int, float)) and isfinite(v) and v > 0 for v in (opening, close)):
        state.pop('entry_body_reference', None)
        return
    state['entry_body_reference'] = dict(open=opening, close=close, end=end, expires=end+1)


def check(observation, parameters, state):
    reference = state.get('entry_body_reference') or {}
    now = observation.observed_at.timestamp()
    if (not reference or not reference['end'] <= now < reference['expires']
            or 'market_data_update' not in observation.evaluation_events):
        return 'entry_body_reference_unavailable', dict(reference)
    threshold = max(reference['open'], reference['close']) + (
        parameters['entry_body_breakout']['offset_ticks'] * parameters['execution']['tick_size'])
    evidence = dict(reference, threshold=threshold, price=observation.price)
    passed = isfinite(observation.price) and observation.price > threshold + 1e-10
    return ('' if passed else 'entry_body_not_broken'), evidence
