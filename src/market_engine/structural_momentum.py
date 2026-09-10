"""Completed-candle momentum evidence; never an order or reversal guarantee."""


def observe(state, close, previous, macd, signal, sequence, settings):
    prior = state.get('last')
    histogram = macd-signal
    gap = settings.macd_gap_bps
    bps = histogram/close*10000
    ready = sequence >= 26
    # Compare raw separation on a common price basis, not changing denominators.
    delta = (histogram-prior['histogram'])/close*10000 if prior else 0.
    step = 1 if delta > settings.macd_change_bps else -1 if delta < -settings.macd_change_bps else 0
    state['steps'] = (state.get('steps', [])+[step])[-settings.momentum_confirm_closes:]
    sustained = step if len(state['steps']) == settings.momentum_confirm_closes and all(x == step for x in state['steps']) else 0
    zone = 'warming_up' if not ready else 'above_gap' if bps >= gap else 'below_gap' if bps <= -gap else 'inside_gap'
    prior_zone = prior['zone'] if prior else 'warming_up'
    if zone == 'inside_gap' and prior_zone != zone:
        state['entered_from'] = prior_zone
    watch = ('watch_up' if sustained == 1 else 'watch_down' if sustained == -1 else 'watch_mixed') if zone == 'inside_gap' else None
    transitions = []
    if ready and prior and prior_zone != 'warming_up':
        if zone != prior_zone:
            transitions.append('entered_'+zone)
        if prior['histogram'] <= 0 < histogram:
            transitions.append('signal_cross_up')
        elif prior['histogram'] >= 0 > histogram:
            transitions.append('signal_cross_down')
        if prior['macd'] <= 0 < macd:
            transitions.append('zero_cross_up')
        elif prior['macd'] >= 0 > macd:
            transitions.append('zero_cross_down')
    tags = ['macd_'+zone]
    if ready:
        tags += ['macd_'+('above_signal' if histogram > 0 else 'below_signal' if histogram < 0 else 'at_signal'),
                 'macd_'+('rising' if sustained == 1 else 'falling' if sustained == -1 else 'mixed')]
        if watch:
            tags.append('macd_'+watch)
        if prior and sustained:
            tags.append('macd_separation_'+('strengthening' if abs(histogram)>abs(prior['histogram']) else 'weakening'))
        tags += ['macd_'+t for t in transitions]
    period = settings.rsi_period
    if previous is not None:
        change = close-previous
        gain, loss = max(change, 0), max(-change, 0)
        count = state.get('rsi_count', 0)+1
        state['rsi_count'] = count
        if count <= period:
            state['gain'] = state.get('gain', 0.)+gain/period
            state['loss'] = state.get('loss', 0.)+loss/period
        else:
            state['gain'] += (gain-state['gain'])/period
            state['loss'] += (loss-state['loss'])/period
    rsi_ready = state.get('rsi_count', 0) >= period
    value = None
    if rsi_ready:
        gain, loss = state['gain'], state['loss']
        value = 50. if gain == loss == 0 else 100. if loss == 0 else 100-100/(1+gain/loss)
    old_rsi = prior.get('rsi') if prior else None
    slope = 'warming_up' if value is None or old_rsi is None else 'rising' if value-old_rsi > settings.rsi_change_points else 'falling' if old_rsi-value > settings.rsi_change_points else 'steady'
    rsi_zone = 'warming_up' if value is None else 'above_neutral' if value > 50+settings.rsi_neutral_band else 'below_neutral' if value < 50-settings.rsi_neutral_band else 'neutral'
    tags += ['rsi_'+rsi_zone, 'rsi_'+slope]
    if old_rsi is not None and value is not None:
        if old_rsi <= 50+settings.rsi_neutral_band < value: tags.append('rsi_cross_above_neutral')
        if old_rsi >= 50-settings.rsi_neutral_band > value: tags.append('rsi_cross_below_neutral')
    if value is not None:
        if value >= 70: tags.append('rsi_elevated')
        if value <= 30: tags.append('rsi_depressed')
    macd_direction = sustained if zone == 'inside_gap' else 1 if zone == 'above_gap' else -1 if zone == 'below_gap' else 0
    rsi_direction = 1 if rsi_zone == 'above_neutral' else -1 if rsi_zone == 'below_neutral' else 0
    agreement = 'warming_up' if not ready or not rsi_ready else 'bullish' if macd_direction == rsi_direction == 1 else 'bearish' if macd_direction == rsi_direction == -1 else 'mixed'
    tags.append('momentum_'+agreement)
    state['last'] = dict(histogram=histogram, macd=macd, zone=zone, rsi=value)
    return dict(tags=list(dict.fromkeys(tags)), agreement=agreement,
        macd=dict(value=macd, signal=signal, histogram=histogram, histogram_bps=bps,
                  delta_bps=delta, zone=zone, watch=watch, entered_from=state.get('entered_from') if watch else None,
                  trend='rising' if sustained == 1 else 'falling' if sustained == -1 else 'mixed',
                  transitions=transitions, gap_bps=gap, periods=[12,26,9], period_unit='candles'),
        rsi=dict(value=value, period=period, method='Wilder', period_unit='candles', warmup=not rsi_ready,
                 observations=state.get('rsi_count',0), zone=rsi_zone, trend=slope))
