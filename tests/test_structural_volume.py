from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.market_engine.structural_detector import DetectorSettings, StructuralDetector
from src.market_engine.structural_volume import VolumeLevels
from src.backend.structural_detector_service import Candle


def bar(i, opened, close, volume=100, duration=1):
    return dict(time=i,end=i+duration,open=opened,close=close,high=max(opened,close)+.01,low=min(opened,close)-.01,volume=volume)


def observe(engine,b,state='advance',events=(),pivots=()):
    return engine.observe(b,pivots,.001,.1,state,events)


def test_volume_missing_zero_and_negative_are_distinct():
    d=StructuralDetector()
    assert d.observe(bar(0,10,10.1,None))['volume_analysis']['status']=='unavailable'
    assert d.observe(bar(1,10.1,10.2,0))['volume_analysis']['divergence']['status']=='zero_volume'
    with pytest.raises(ValueError): d.observe(bar(2,10.2,10.3,-1))
    with pytest.raises(ValueError): Candle(**bar(2,10,10.1,float('nan')))


def test_volume_rate_normalizes_duration_and_uses_prior_baseline():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1))
    observe(d,bar(0,10,10.1,100,1))
    v,_=observe(d,bar(1,10.1,10.2,200,2))
    assert v['previous_ratio']==v['relative_volume']==1
    assert v['baseline_rate']==100
    assert v['divergence']['direction']=='none'


def test_mirrored_divergence_and_causal_confirmation():
    results=[]
    for sign in (1,-1):
        d=VolumeLevels(DetectorSettings(volume_warmup_candles=1,volume_divergence_min_score=20))
        observe(d,bar(0,10,10+sign*.1,1000),state='advance' if sign==1 else 'decline')
        v,_=observe(d,bar(1,10+sign*.1,10+sign*.2,200))
        frozen=deepcopy(v)
        assert v['divergence']['direction']==('bearish' if sign==1 else 'bullish')
        assert v['reversal_candidates'] and not v['reversal_outcomes']
        v2,_=observe(d,bar(2,10+sign*.2,10,400),events=[{'state':'support_failure' if sign==1 else 'breakout'}])
        assert v2['reversal_outcomes'][0]['outcome']=='structural_reversal_confirmation'
        assert v==frozen
        results.append(v['divergence']['score'])
    assert results[0]==results[1]


def test_green_volume_is_not_buy_flow_and_close_progress_is_separate():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1))
    observe(d,bar(0,10,10.5,1000))
    v,_=observe(d,bar(1,10.1,10.2,100))
    assert v['color']=='green'
    assert v['divergence']['direction']=='bullish'  # Close falls despite green body.
    assert v['color_basis']=='close_vs_open_not_buy_sell_flow'


def test_ranked_swings_known_only_after_confirmation_and_session_resets():
    d=VolumeLevels(DetectorSettings())
    at=datetime(2026,8,21,9,30,tzinfo=ZoneInfo('America/New_York')).timestamp()
    _,before=observe(d,bar(at,10,10.2))
    pivots=[dict(side='resistance',price=p,pivot_at=at+1) for p in (10.1,10.3,10.2,10.3)]
    _,after=observe(d,bar(at+1,10.2,10.1),pivots=pivots)
    assert before['ranked_highs']==[]
    assert [p['price'] for p in after['ranked_highs']]==[10.3,10.2,10.1]
    assert not after['full_session_verified']
    v,following=observe(d,bar(at+86400,9,9.1))
    assert following['ranked_highs']==[] and following['high']['price']==9.11
    assert v['prior_samples']==0


def test_volume_patterns_distinguish_effort_and_result():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1))
    observe(d,bar(0,10,10.2,100))
    v,_=observe(d,bar(1,10.2,10.2,300),events=[{'state':'rejection'}])
    assert 'high_effort_low_progress' in v['tags'] and 'high_volume_rejection' in v['tags']
    assert v['divergence']['direction']=='none'
    v,_=observe(d,bar(2,10.2,10.1,100),state='pullback')
    assert 'countermove_volume_fading' in v['tags']
    v,_=observe(d,bar(3,10.1,10.3,50),state='advance')
    assert 'red_to_green' in v['tags'] and 'recovery_after_countermove' in v['tags']


def test_candidate_expires_without_rewriting_earlier_labels():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1,volume_setup_max_candles=1,volume_divergence_min_score=10))
    observe(d,bar(0,10,10.1,1000))
    candidate,_=observe(d,bar(1,10.1,10.2,100))
    observe(d,bar(2,10.2,10.2,100))
    result,_=observe(d,bar(3,10.2,10.2,100))
    assert result['reversal_outcomes'][0]['outcome']=='expired_unconfirmed'
    assert candidate['reversal_outcomes']==[]


def test_volume_supported_continuation_invalidates_exhaustion_warning():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1,volume_divergence_min_score=10))
    observe(d,bar(0,10,10.1,1000))
    observe(d,bar(1,10.1,10.2,100))
    result,_=observe(d,bar(2,10.2,10.4,500))
    assert not result['reversal_candidates']
    assert result['reversal_outcomes'][0]['outcome']=='invalidated_by_volume_supported_continuation'


def test_continuing_down_is_not_recovery_from_uptrend_pullback():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1))
    observe(d,bar(0,10,9.9),state='pullback')
    result,_=observe(d,bar(1,9.9,9.8),state='decline')
    assert 'recovery_after_countermove' not in result['tags']


def test_volume_correction_recomputes_cache_and_forming_bar_is_excluded():
    from src.backend.structural_detector_service import calculate,DetectorRequest,_cache
    class NoGlobal:
        book=None
        def __init__(self,*_): pass
        def at(self,bar): return None,'unavailable'
    _cache.clear()
    request=dict(ticker='TEST',timeframe='1s',as_of=2,candles=[bar(0,10,10.1,100),bar(1,10.1,10.2,50),bar(2,10.2,10.3,100000)])
    before=calculate(DetectorRequest(**request),NoGlobal)
    frozen=deepcopy(before)
    request['candles'][1]['volume']=200
    after=calculate(DetectorRequest(**request),NoGlobal)
    assert before==frozen
    assert len(after['rows'])==2 and after['pending_count']==1
    assert after['rows'][1]['volume_analysis']['previous_ratio']==2
    assert before['rows'][1]['volume_analysis']['previous_ratio']==.5
    _cache.clear()


def test_hod_wick_rejection_is_not_a_close_break():
    d=VolumeLevels(DetectorSettings(volume_warmup_candles=1))
    observe(d,bar(0,10,10.2))
    b=bar(1,10.2,10.1);b['high']=10.3
    v,levels=observe(d,b)
    assert 'observed_hod_rejection' in v['tags']
    assert 'observed_hod_break' not in v['tags']
    assert levels['prior_high']['price']==pytest.approx(10.21) and levels['high']['price']==10.3
