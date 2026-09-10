import json
from copy import deepcopy

import pytest

from src.market_engine.structural_detector import StructuralDetector, DetectorSettings
from src.market_engine.structural_detector_checkpoint import checkpoint, restore
from src.market_engine.structural_momentum import observe


def test_watch_preserves_negative_position_and_records_entry():
    settings=DetectorSettings()
    state={}
    rows=[observe(state,100,100,x,0,30+i,settings) for i,x in enumerate([-.3,-.2,-.1,-.05,.01,.3])]
    assert rows[2]['macd']['watch']=='watch_up'
    assert rows[2]['macd']['entered_from']=='below_gap'
    assert 'macd_below_signal' in rows[2]['tags']
    assert 'signal_cross_up' in rows[4]['macd']['transitions']
    assert rows[4]['macd']['zone']=='inside_gap'
    assert rows[-1]['macd']['zone']=='above_gap'


def test_watch_is_symmetric_and_chop_is_mixed():
    settings=DetectorSettings()
    up={};down={}
    for i,x in enumerate([-.3,-.2,-.1]):
        a=observe(up,100,100,x,0,30+i,settings)
        b=observe(down,100,100,-x,0,30+i,settings)
    assert a['macd']['watch']=='watch_up' and b['macd']['watch']=='watch_down'
    up={}
    for i,x in enumerate([.01,-.01,.01,-.01]):
        row=observe(up,100,100,x,0,40+i,settings)
        assert row['macd']['watch']=='watch_mixed'


@pytest.mark.parametrize('changes,expected', [([0,0,0,0],50),([1,1,1,1],100),([-1,-1,-1,-1],0),([1,-1,2,-2],50)])
def test_wilder_seed_and_next_value(changes,expected):
    settings=DetectorSettings(rsi_period=4);state={};price=100
    for i,change in enumerate(changes):
        row=observe(state,price+change,price,0,0,i+2,settings);price+=change
        assert row['rsi']['warmup']==(i<3)
    assert row['rsi']['value']==pytest.approx(expected)
    gain=(sum(max(x,0) for x in changes)/4*3+1)/4
    loss=sum(max(-x,0) for x in changes)/4*3/4
    expected=100 if loss==0 else 100-100/(1+gain/loss)
    assert observe(state,price+1,price,0,0,6,settings)['rsi']['value']==pytest.approx(expected)


def test_checkpoint_prefix_gap_and_scale_invariance():
    engine=StructuralDetector();scaled=StructuralDetector();rows=[]
    for i in range(70):
        close=100+i*.1 if i<35 else 103.5-(i-35)*.15
        bar=dict(time=i,end=i+1,open=close,high=close+.1,low=close-.1,close=close,volume=100)
        if i==40:
            resumed=restore(json.loads(json.dumps(checkpoint(engine))))
            frozen=deepcopy(rows)
        row=engine.observe(bar);rows.append(row)
        other=scaled.observe({k:v*10 if k in ('open','high','low','close') else v for k,v in bar.items()})
        assert row['momentum']['tags']==other['momentum']['tags']
        if i>=40: assert row==resumed.observe(bar)
    assert rows[:40]==frozen
    reset=engine.observe(dict(bar,time=100,end=101))
    assert reset['momentum']['rsi']['value'] is None
    assert reset['momentum']['macd']['zone']=='warming_up'


def test_service_settings_match_and_reject_invalid_momentum():
    from src.backend.structural_detector_service import Settings
    from dataclasses import asdict
    assert Settings().model_dump()==asdict(DetectorSettings())
    for args in [dict(rsi_period=0),dict(rsi_period=2.5),dict(momentum_confirm_closes=0),dict(rsi_neutral_band=30)]:
        with pytest.raises(ValueError): DetectorSettings(**args)


def test_http_momentum_matches_direct_detector_and_excludes_forming_candle(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.backend import structural_detector_service as service
    class NoGlobal:
        book=None
        def __init__(self,*args): pass
        def at(self,bar): return None,'unavailable'
    calculate=service.calculate
    monkeypatch.setattr(service,'calculate',lambda request:calculate(request,NoGlobal))
    app=FastAPI();app.include_router(service.router)
    bars=[dict(time=i,end=i+1,open=100+i*.1,close=100+i*.1,high=100.1+i*.1,low=99.9+i*.1,volume=100) for i in range(45)]
    with TestClient(app) as client:
        response=client.post('/api/indicators/structural-detector',json=dict(ticker='MOMTEST',timeframe='1s',as_of=44,candles=bars,settings=dict(rsi_period=14)))
    assert response.status_code==200
    result=response.json();engine=StructuralDetector()
    assert result['rows']==[engine.observe(b) for b in bars[:44]]
    assert result['pending_count']==1 and result['rows'][-1]['momentum']['rsi']['value']==100


def test_reversal_support_is_context_not_confirmation(monkeypatch):
    engine=StructuralDetector(DetectorSettings(macd_gap_bps=.1))
    for i in range(40):
        close=100+i*.1
        row=engine.observe(dict(time=i,end=i+1,open=close,close=close,high=close+.1,low=close-.1,volume=100))
    volume=deepcopy(row['volume_analysis'])
    volume['reversal_candidates']=[dict(direction='bullish',score=40),dict(direction='bearish',score=40)]
    volume['reversal_outcomes']=[]
    original=engine.volume_levels.observe
    def patched(*args,**kwargs):
        _,levels=original(*args,**kwargs)
        return deepcopy(volume),levels
    monkeypatch.setattr(engine.volume_levels,'observe',patched)
    row=engine.observe(dict(time=40,end=41,open=104,close=104,high=104.1,low=103.9,volume=100))
    candidates=row['volume_analysis']['reversal_candidates']
    assert [c['momentum_context'] for c in candidates]==['supports','opposes']
    assert not row['volume_analysis']['reversal_outcomes']
    assert 'momentum_context' not in volume['reversal_candidates'][0]
