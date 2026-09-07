"""Causal v4 momentum policy shared by research replay and strategy execution.

No ticker, session, hindsight label, or future outcome is an input.
"""
from math import isfinite, floor

CONTRACT = 'swing-v4-momentum-v1'
DEFAULTS = dict(minimum_p_norm=.2, minimum_reward_risk=1., maximum_risk_bps=300.,
                require_green=True, resistance_rejection_exit=True, minimum_progress_spreads=1.)


def configure(parameters):
    if parameters['swing_momentum_contract'] != CONTRACT:
        raise ValueError('Unknown swing momentum contract')
    if not parameters.get('swing_evidence_contract'):
        raise ValueError('Swing momentum requires the causal MACD evidence base')
    settings=dict(DEFAULTS,**parameters.get('swing_momentum',{}))
    for key in ('minimum_p_norm','minimum_reward_risk','maximum_risk_bps','minimum_progress_spreads'):
        value=settings[key]
        if not isinstance(value,(int,float)) or not isfinite(value) or value<0:
            raise ValueError('Invalid swing momentum '+key)
    if settings['minimum_p_norm']>1 or settings['maximum_risk_bps']==0:
        raise ValueError('Invalid swing momentum threshold')
    parameters['swing_momentum']=settings


def update_progress(state, now, price):
    history=[r for r in state.get('swing_price_history',[]) if now-4<=r[0]<now]
    reference=next((p for t,p in reversed(history) if t<=now-3),None)
    state['swing_price_history']=[*history,(now,price)]
    state['swing_price_progress']=price-reference if reference is not None else None


def observation_context(observation,parameters):
    return context((*observation.structural_support_levels,*observation.structural_resistance_levels),
                   observation.price,observation.observed_at.timestamp(),parameters['swing_momentum']['minimum_p_norm'])


def context(levels, price, now, minimum_p_norm=.2):
    valid=[]
    for row in levels:
        try:
            lower=float(row.get('band_lower',row['lower']))
            upper=float(row.get('band_upper',row['upper']))
            score=float(row['p_norm']); confirmed=float(row['confirmed_at_ms'])/1000
            created=float(row['created_at_ms'])/1000
            if (row.get('book_version')!='causal-swing-closing-book-4'
                    or row.get('lifecycle')!='active' or not minimum_p_norm<=score<=1
                    or not all(isfinite(x) for x in (lower,upper,confirmed,created))
                    or not 0<lower<=upper or max(confirmed,created)>now):
                continue
            valid.append(dict(lower=lower,upper=upper,confirmed=confirmed,
                              side=row['side'],id=str(row['unified_level_id'])))
        except (KeyError,ValueError,TypeError):
            continue
    supports=[l for l in valid if l['side']==1 and l['upper']<price]
    resistances=[l for l in valid if l['side']==-1 and l['upper']>=price]
    return dict(support=max(supports,key=lambda l:l['lower'],default=None),
                resistance=min(resistances,key=lambda l:l['lower'],default=None))


def initial_stop(ctx, price, red_close, tick):
    candidates=[red_close-tick] if 0<red_close<price else []
    if ctx.get('support'):
        candidates.append(ctx['support']['lower']-tick)
    candidate=max(candidates,default=0.)
    return floor((candidate+tick*1e-9)/tick)*tick if 0<candidate<price else 0.


def entry(ctx, price, opening, stop, settings, *, progress=None, spread=None):
    if settings['require_green'] and not price>opening>0:
        return 'swing_entry_not_green'
    if not ctx.get('support') and not ctx.get('resistance'):
        return 'swing_v4_levels_unavailable'
    if not 0<stop<price:
        return 'swing_stop_unavailable'
    if settings.get('minimum_progress_spreads',0)>0:
        if progress is None or spread is None or not isfinite(progress) or not isfinite(spread) or spread<=0:
            return 'swing_progress_unavailable'
        if progress<=spread*settings['minimum_progress_spreads']:
            return 'swing_progress_too_small'
    risk=price-stop
    if risk/price*10000>settings['maximum_risk_bps']:
        return 'swing_entry_risk_too_wide'
    resistance=ctx.get('resistance')
    if resistance:
        if price>=resistance['lower']:
            return 'swing_entry_inside_resistance'
        if resistance['lower']-price<settings['minimum_reward_risk']*risk:
            return 'swing_entry_insufficient_room'
    return ''


def manage(ctx, price, now, entry_at, stop, tick, state, settings):
    """Track one tested boundary; only new confirmed supports tighten the stop."""
    support=ctx.get('support')
    if support and entry_at<support['confirmed']<=now:
        stop=max(stop,floor((support['lower']-tick+tick*1e-9)/tick)*tick)
    if state.get('entry_at')!=entry_at:
        state.clear();state['entry_at']=entry_at
    previous=state.get('previous_price')
    tested=state.get('tested')
    reason=''
    if tested:
        if price>tested['upper']:
            state.pop('tested',None)
        elif price<tested['lower'] and now>tested['at']:
            reason='swing_resistance_rejected'
            state.pop('tested',None)
    resistance=ctx.get('resistance')
    if (not state.get('tested') and resistance and previous is not None
            and previous<resistance['lower']<=price<=resistance['upper']):
        state['tested']=dict(resistance,at=now)
    state['previous_price']=price
    return stop, reason if settings['resistance_rejection_exit'] else ''
