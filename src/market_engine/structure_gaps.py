"""Causal price-space gaps and frozen pre-touch support opportunity geometry."""
from math import isfinite


def gaps(levels):
    output=[]
    for side,kind in ((1,'support'),(-1,'resistance')):
        ordered=sorted((l for l in levels if l['side']==side),key=lambda l:l['lower'])
        if not ordered:continue
        previous=ordered[0]
        for row in ordered[1:]:
            if row['lower']>previous['upper']:
                output.append(dict(kind=kind,lower=previous['upper'],upper=row['lower'],
                    id=f"{kind}:{previous['unified_level_id']}:{row['unified_level_id']}"))
            if row['upper']>previous['upper']:previous=row
    return output


class GapAnalyzer:
    def __init__(self, proximity_bps=50., cost_bps=20., maximum_gaps=3, minimum_gap_bps=20.):
        self.proximity_bps=proximity_bps;self.cost_bps=cost_bps
        self.maximum_gaps=maximum_gaps;self.minimum_gap_bps=minimum_gap_bps
        self.segments=[];self.current={};self.setups=[];self.pending=[]
        self.last_price=None;self.last_time=None;self.encounters=set()

    def observe(self,t,high,low,price,levels,noise):
        if self.last_time is not None and t<=self.last_time:raise ValueError('Bars must be strictly ordered')
        if not all(isfinite(x) for x in (t,high,low,price,noise)) or not 0<low<=price<=high or noise<0:
            raise ValueError('Invalid causal bar')
        if any(max(l['created_at_ms'],l['confirmed_at_ms'])>t*1000 for l in levels):
            raise ValueError('Future level supplied')
        tick=.01 if price>=1 else .0001
        cost=price*self.cost_bps/10000
        selected=[]
        all_gaps=gaps(levels)
        for kind in ('support','resistance'):
            candidates=[g for g in all_gaps if g['kind']==kind and (g['upper']-g['lower'])/price*10000>=self.minimum_gap_bps]
            candidates.sort(key=lambda g:(max(g['lower']-price,price-g['upper'],0),g['lower']))
            selected.extend(candidates[:self.maximum_gaps])
        alive=set()
        for g in selected:
            key=(g['id'],g['lower'],g['upper']);alive.add(key)
            if key not in self.current:
                self.current[key]=len(self.segments)
                self.segments.append(dict(g,valid_from=t,valid_to=None,width=g['upper']-g['lower'],
                    width_bps=(g['upper']-g['lower'])/price*10000,
                    width_volatility=(g['upper']-g['lower'])/noise if noise>0 else None))
        for key in list(self.current):
            if key not in alive:self.segments[self.current.pop(key)]['valid_to']=t
        # Outcomes are separate fields with their own availability timestamps.
        pending=[]
        for index in self.pending:
            s=self.setups[index]
            stop=low<=s['stop'];target=high>=s['target']
            if stop or target:
                s['outcome']='ambiguous_same_bar' if stop and target else 'target_first' if target else 'stop_first'
                s['outcome_at']=t
            else:pending.append(index)
        self.pending=pending
        for support in levels:
            if support['side']!=1:continue
            key=(support['unified_level_id'],support['lower'],support['upper'])
            upper=support['upper'];zone=upper*(1+self.proximity_bps/10000)
            if price>zone* (1+self.proximity_bps/10000):self.encounters.discard(key)
            # Pre-touch only: a bar already touching support cannot be claimed
            # as a prediction made before that touch.
            if (key in self.encounters or self.last_price is None or self.last_price<=zone
                    or support['confirmed_at_ms']>self.last_time*1000
                    or not upper<low<=price<=zone):continue
            resistances=[l for l in levels if l['side']==-1 and l['upper']>=price]
            resistance=min(resistances,key=lambda l:l['lower'],default=None)
            if resistance is None or resistance['lower']<=price:continue
            target=resistance['lower']-max(tick,cost/2)
            stop=support['lower']-max(tick,noise/2)
            room=target-price-cost;risk=price-stop+cost
            if room<=0 or risk<=0:continue
            score=100*room/(room+risk+noise)
            self.encounters.add(key)
            self.pending.append(len(self.setups))
            self.setups.append(dict(id=len(self.setups)+1,time=t,price=price,support=upper,
                support_lower=support['lower'],stop=stop,target=target,score=score,
                upside=room,risk=risk,reward_risk=room/risk,volatility=noise,cost=cost,
                upside_gap=resistance['lower']-upper,
                downside_gap=min((g['upper']-g['lower']
                    for g in all_gaps if g['kind']=='support' and g['upper']==support['lower']),default=None),
                support_age_seconds=t-support['confirmed_at_ms']/1000,
                support_prominence=support['prominence'],support_p_norm=support['p_norm'],
                outcome='pending',outcome_at=None))
        if len(self.segments)>30000 or len(self.setups)>10000:
            raise ValueError('Preview budget exceeded; narrow the requested window')
        self.last_price=price;self.last_time=t
