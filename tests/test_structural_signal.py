from copy import deepcopy
import pytest
from src.market_engine.structural_signal import observe
from src.market_engine.structural_detector import DetectorSettings


def row(i,event=None,close=10.2,long=True,volume=100):
    level=dict(lower=10.,upper=10.1,side=-1,confirmed_at=0)
    if not long:
        close=20-close;level=dict(lower=9.9,upper=10.,side=1,confirmed_at=0)
    bar=dict(time=i,end=i+1,open=close,close=close,low=close-.01,high=close+.01,volume=volume)
    return dict(candle=bar,effective_at=i+1,sequence=i,gap_before=False,
        global_events=[dict(state=event,level=level,band_id='test',context=dict(significance='outer'))] if event else [],local_events=[],
        qualification=dict(ready=True,atr=.1),momentum=dict(agreement='bullish' if long else 'bearish',macd=dict(watch=None)))


def levels(long):
    return [dict(lower=11,upper=11.1,side=-1,confirmed_at=0)] if long else [dict(lower=8.9,upper=9,side=1,confirmed_at=0)]


@pytest.mark.parametrize('long',[True,False])
def test_symmetric_lifecycle_and_frozen_references(long):
    state={};settings=DetectorSettings();direction='long' if long else 'short'
    events=['breakout','breakout_accepted','support_retest_held'] if long else ['support_failure','breakdown_accepted','resistance_retest_held']
    outputs=[observe(state,row(i+30,e,long=long),levels(long),settings) for i,e in enumerate(events)]
    assert [r['action'] for r in outputs]==[direction+'_armed',direction+'_accepted',direction+'_enter']
    frozen=deepcopy(outputs)
    hold=observe(state,row(33,long=long),[],settings)
    assert hold['action']==direction+'_hold'
    assert hold['setup']['stop']==outputs[-1]['setup']['stop']
    end=row(34,long=long)
    end['candle']['high']=12;end['candle']['low']=8
    exit_row=observe(state,end,[],settings)
    assert exit_row['action']==direction+'_exit'
    assert exit_row['reason']=='both_boundaries_touched_order_unknown'
    assert outputs==frozen and state['setup'] is None


def test_wrong_level_no_confirmation_then_expiry():
    state={};s=DetectorSettings(signal_setup_candles=2)
    observe(state,row(30,'breakout'),levels(True),s)
    other=row(31,'support_retest_held');other['global_events'][0]['level']['lower']=9.8
    assert observe(state,other,levels(True),s)['phase']=='armed'
    assert observe(state,row(33),levels(True),s)['reason']=='setup_expired'


def test_no_future_target_no_entry_without_momentum_or_volume():
    s=DetectorSettings(signal_confirmation_candles=1);state={}
    future=levels(True);future[0]['confirmed_at']=100
    assert observe(state,row(30,'breakout'),future,s)['phase']=='idle'
    observe(state,row(30,'breakout'),levels(True),s)
    candidate=row(31,'support_retest_held',volume=None)
    assert observe(state,candidate,levels(True),s)['reason']=='positive_volume_unavailable'
    candidate=row(32);candidate['momentum']['agreement']='bearish'
    assert observe(state,candidate,levels(True),s)['reason']=='await_supportive_momentum'
    assert observe(state,row(33),levels(True),s)['reason']=='confirmation_expired'


def test_reset_and_no_same_candle_reentry():
    state={};s=DetectorSettings()
    observe(state,row(30,'breakout'),levels(True),s)
    reset=row(31);reset['gap_before']=True
    assert observe(state,reset,levels(True),s)['reason']=='context_reset'
    assert state['setup'] is None


@pytest.mark.parametrize('long',[True,False])
def test_full_detector_signal_prefix_and_active_checkpoint(long):
    import json
    from src.market_engine.structural_detector import StructuralDetector
    from src.market_engine.structural_detector_checkpoint import checkpoint,restore
    engine=StructuralDetector(DetectorSettings(macd_gap_bps=.1))
    bands=[dict(lower=p-.01,upper=p+.01,price=p,side=side,confirmed_at_ms=0,unified_level_id=str(p),scale='global') for p,side in [(9,1),(10.1,-1),(11,-1)]]
    prices=[10+i*.002 for i in range(40)]+[10.2,10.22,10.23,10.19,10.24,10.25,10.26,10.27,10.28,11.02]
    if not long:
        bands=[dict(b,lower=20-b['upper'],upper=20-b['lower'],price=20-b['price'],side=-b['side']) for b in bands]
    rows=[];bars=[]
    for i,close in enumerate(prices):
        opened=prices[i-1] if i else close
        bar=dict(time=i,end=i+1,open=opened,close=close,high=max(close,opened)+.003,low=10.08 if i==43 else min(close,opened)-.003,volume=1000)
        if not long: bar=dict(bar,open=20-bar['open'],close=20-bar['close'],high=20-bar['low'],low=20-bar['high'])
        bars.append(bar);result=engine.observe(bar,bands,'available');rows.append(result)
        if i==44:
            saved=deepcopy(rows)
            resumed=restore(json.loads(json.dumps(checkpoint(engine))))
        elif i>44: assert resumed.observe(bar,bands,'available')==result
    direction='long' if long else 'short'
    assert rows[44]['technical_signal']['action']==direction+'_enter'
    assert rows[45]['technical_signal']['action']==direction+'_hold'
    assert rows[49]['technical_signal']['action']==direction+'_exit'
    assert rows[:45]==saved
    replay=StructuralDetector(DetectorSettings(macd_gap_bps=.1))
    assert [replay.observe(b,bands,'available') for b in bars[:45]]==saved
