from copy import deepcopy
import json
import pytest
from src.market_engine.structural_signal import observe,level_map
from src.market_engine.structural_detector import StructuralDetector,DetectorSettings
from src.market_engine.structural_detector_checkpoint import checkpoint,restore


def band(p,side=-1,**kw):
    return dict(lower=p-.05,upper=p+.05,price=p,side=side,unified_level_id=str(p)+':'+str(side),confirmed_at=0,scale='major',**kw)


def row(i,event=None,close=10.2,long=True,volume=100,origin=None):
    level=origin or band(10.1)
    bar=dict(time=i,end=i+1,open=close-.08,close=close,low=close-.1,high=close+.1,volume=volume)
    if not long:
        bar=dict(bar,open=20-bar['open'],close=20-bar['close'],low=20-bar['high'],high=20-bar['low'])
        level=dict(level,lower=20-level['upper'],upper=20-level['lower'],side=-level['side'],price=20-level['price'])
    return dict(candle=bar,effective_at=i+1,sequence=i,gap_before=False,
        global_events=[dict(state=event,level=level,band_id='test',break_at=30,encounters=1)] if event else [],local_events=[],
        qualification=dict(ready=True,atr=.1),momentum=dict(agreement='bullish' if long else 'bearish',macd=dict(watch=None,trend='rising' if long else 'falling'),rsi=dict(trend='rising' if long else 'falling')))


def levels(long=True):
    values=[band(9.8,1),band(10.1),band(11)]
    return values if long else [dict(l,lower=20-l['upper'],upper=20-l['lower'],price=20-l['price'],side=-l['side']) for l in values]


@pytest.mark.parametrize('long',[True,False])
@pytest.mark.parametrize('pattern,up,down',[('initiation','breakout','support_failure'),('continuation','support_retest_held','resistance_retest_held'),('reversal','failed_breakdown','failed_breakout')])
def test_three_entry_paths_are_direction_symmetric(pattern,up,down,long):
    state={};origin=band(10.1,1 if pattern=='reversal' else -1)
    result=observe(state,row(30,up if long else down,long=long,origin=origin),levels(long),DetectorSettings())
    assert result['action']==('long_enter' if long else 'short_enter')
    assert result['setup']['pattern']==pattern
    assert result['setup']['initial_stop']==result['setup']['stop']


def test_major_barrier_blocks_and_minor_levels_do_not_invent_room():
    s=DetectorSettings();trigger=row(30,'breakout')
    near=band(10.3);near['reversal_distance']=.3
    r=observe({},trigger,levels()+[near],s)
    assert r['action']=='wait' and r['reason']=='insufficient_structural_room'
    minor=dict(near,scale='local',reversal_distance=.02)
    assert observe({},trigger,levels()+[minor],s)['action']=='long_enter'
    extended=row(30,'breakout',close=10.8)
    assert observe({},extended,levels(),s)['reason']=='invalid_or_excessive_risk'


def test_future_levels_cannot_change_decision_and_zones_merge_without_double_counting():
    s=DetectorSettings();trigger=row(30,'breakout')
    future=dict(band(10.3),confirmed_at=31)
    assert observe({},trigger,levels(),s)==observe({},trigger,levels()+[future],s)
    z=level_map([band(11),band(11.11)],10,.1,30,{},.2)
    assert len(z)==1 and z[0]['importance']==3 and len(z[0]['members'])==2


def test_crossing_one_level_cannot_hide_the_rest_of_its_resistance_zone():
    r=observe({},row(30,'breakout'),levels()+[band(10.21)],DetectorSettings())
    assert r['action']=='wait' and r['reason']=='insufficient_structural_room'


def test_coincident_local_level_cannot_erase_major_evidence():
    major=band(11);minor=dict(major,scale='local',reversal_distance=.01)
    for values in ([major,minor],[minor,major]):
        z=level_map(values,10,.1,30,{},.2)
        assert len(z)==1 and z[0]['importance']==3 and len(z[0]['members'])==2


def test_open_room_is_explicit_and_volume_is_required():
    s=DetectorSettings();r=observe({},row(30,'breakout'),levels()[:-1],s)
    assert r['action']=='long_enter' and r['setup']['target'] is None
    assert r['setup']['reward_risk'] is None and 'not_forecast' in r['setup']['room_basis']
    assert observe({},row(30,'breakout',volume=None),levels(),s)['reason']=='positive_volume_unavailable'


@pytest.mark.parametrize('long',[True,False])
def test_thin_level_stop_respects_volatility_floor(long):
    s=DetectorSettings();origin=dict(band(10.19),lower=10.189,upper=10.191)
    r=observe({},row(30,'breakout' if long else 'support_failure',long=long,origin=origin),levels(long),s)
    assert r['action']==('long_enter' if long else 'short_enter')
    assert abs(r['setup']['entry_reference']-r['setup']['stop'])==pytest.approx(.1)


def test_stop_tightens_after_close_never_retroactively_or_widens():
    state={};s=DetectorSettings();entry=observe(state,row(30,'breakout'),levels(),s);frozen=deepcopy(entry)
    current=row(31,close=10.5);current['candle']['low']=10.08
    current['confirmed_swings']=[dict(band(10.3,1),confirmed_at=32)]
    held=observe(state,current,levels(),s)
    assert held['action']=='long_hold' and held['setup']['stop']>entry['setup']['stop']
    stop=held['setup']['stop']
    later=observe(state,row(32,close=10.5),[band(9,1)],s)
    assert later['setup']['stop']==stop
    crossed=row(33,close=10.5);crossed['candle']['low']=stop-.01
    assert observe(state,crossed,levels(),s)['reason']=='stop_reference_touched'
    assert entry==frozen


def test_opposing_structure_and_momentum_exit_before_original_stop():
    state={};s=DetectorSettings();entry=observe(state,row(30,'breakout'),levels(),s)
    current=row(31,'support_failure',close=10.4,origin=band(10.45,1))
    current['momentum']['agreement']='bearish';current['momentum']['macd']['trend']='falling'
    result=observe(state,current,levels()+[band(10.45,1)],s)
    assert current['candle']['low']>entry['setup']['stop']
    assert result['action']=='long_exit' and result['reason']=='opposing_structure_and_momentum'


def test_accepted_barrier_advances_only_after_confirmation():
    state={};s=DetectorSettings();observe(state,row(30,'breakout'),levels(),s)
    touched=observe(state,row(31,close=10.96),levels()+[band(12)],s)
    assert touched['action']=='long_hold' and touched['setup']['target']==pytest.approx(10.95)
    accepted=row(32,'breakout_accepted',close=11.2,origin=band(11))
    advanced=observe(state,accepted,levels()+[band(12)],s)
    assert advanced['setup']['target']==pytest.approx(11.95)
    assert 'accepted_barrier_advance' in advanced['management']


def test_stale_trigger_not_reused_after_exit_and_gap_resets():
    state={};s=DetectorSettings();observe(state,row(30,'breakout'),levels(),s)
    exit_row=row(31,close=10.2);exit_row['candle']['low']=9
    assert observe(state,exit_row,levels(),s)['action']=='long_exit'
    assert observe(state,row(32,'breakout'),levels(),s)['action']=='wait'
    reset=row(40);reset['gap_before']=True
    assert observe(state,reset,levels(),s)['reason']=='context_reset'
    assert not state.get('setup')


def test_old_or_minor_structure_does_not_tighten_and_forming_events_are_safe():
    state={};s=DetectorSettings();entry=observe(state,row(30,'breakout'),levels(),s)
    current=row(31,close=10.5)
    current['local_events']=[dict(state='resistance_forming',level=dict(price=10.6,confirmed_at=None))]
    old=band(10.3,1)
    minor=dict(band(10.35,1),confirmed_at=32,scale='local',reversal_distance=.01)
    held=observe(state,current,levels()+[old,minor],s)
    assert held['action']=='long_hold' and held['setup']['stop']==entry['setup']['stop']


def test_active_position_exits_explicitly_on_context_gap():
    engine=StructuralDetector(DetectorSettings(macd_gap_bps=.1))
    prices=[10+i*.002 for i in range(40)]+[10.2]
    for i,c in enumerate(prices):
        o=prices[i-1] if i else c
        last=engine.observe(dict(time=i,end=i+1,open=o,close=c,high=max(c,o)+.03,low=min(c,o)-.03,volume=1000),levels(),'available')
    assert last['technical_signal']['action']=='long_enter'
    result=engine.observe(dict(time=50,end=51,open=10.2,close=10.2,low=10.1,high=10.3,volume=1000),levels(),'available')
    assert result['technical_signal']['action']=='long_exit'
    assert result['technical_signal']['reason']=='context_gap_position_invalidated'
    assert engine.signal_state.get('setup') is None


@pytest.mark.parametrize('long',[True,False])
def test_full_detector_prefix_and_active_checkpoint(long):
    engine=StructuralDetector(DetectorSettings(macd_gap_bps=.1));rows=[];bars=[]
    prices=[10+i*.002 for i in range(40)]+[10.2,10.22,10.23,10.24,10.25,10.26,10.27,10.28,10.3,9.9]
    bands=levels(long)
    for i,c in enumerate(prices):
        o=prices[i-1] if i else c
        b=dict(time=i,end=i+1,open=o,close=c,high=max(c,o)+.03,low=min(c,o)-.03,volume=1000)
        if not long:b=dict(b,open=20-b['open'],close=20-b['close'],high=20-b['low'],low=20-b['high'])
        bars.append(b);r=engine.observe(b,bands,'available');rows.append(r)
        if i==42: frozen=deepcopy(rows);resumed=restore(json.loads(json.dumps(checkpoint(engine))))
        elif i>42: assert r==resumed.observe(b,bands,'available')
    direction='long' if long else 'short'
    assert rows[40]['technical_signal']['action']==direction+'_enter'
    assert rows[41]['technical_signal']['action']==direction+'_hold'
    assert rows[-1]['technical_signal']['action']==direction+'_exit'
    assert rows[:43]==frozen
    fresh=StructuralDetector(DetectorSettings(macd_gap_bps=.1))
    assert [fresh.observe(b,bands,'available') for b in bars[:43]]==frozen
