from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.market_engine.structural_detector import StructuralDetector, DetectorSettings
from src.backend.structural_detector_service import DetectorRequest, calculate, router, _cache


def candle(t, opened, close, high=None, low=None, duration=1):
    return dict(time=t, end=t+duration, open=opened, close=close,
                high=high if high is not None else max(opened, close)+.01,
                low=low if low is not None else min(opened, close)-.01)


def level(price=10.2, side=-1):
    return dict(unified_level_id='resistance-a', lower=price-.01, upper=price+.01,
                price=price, side=side, confirmed_at_ms=0, book_version='causal-swing-closing-book-5')


class NoGlobal:
    book = None
    calls = 0
    def __init__(self, *_):
        pass
    def at(self, bar):
        NoGlobal.calls += 1
        return None, 'no_certified_v5_book_for_session'


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    NoGlobal.calls = 0
    yield
    _cache.clear()


def test_all_candles_classified_outside_macd_and_without_positions():
    engine = StructuralDetector()
    rows = [engine.observe(candle(i, 10-i*.01, 9.99-i*.01)) for i in range(40)]
    assert len(rows)==40
    assert rows[0]['state']=='unknown'
    assert rows[-1]['macd']['histogram_bps']<0
    assert all(r['state']!='inactive' for r in rows)
    assert rows[-1]['state']=='decline'


def test_pullback_is_not_automatically_rejection_or_resistance():
    engine = StructuralDetector()
    for i in range(5):
        engine.observe(candle(i, 10+i*.1, 10.1+i*.1))
    row = engine.observe(candle(5, 10.5, 10.48))
    assert row['state']=='pullback'
    assert not any(e['state'] in ('rejection', 'resistance_forming') for e in row['local_events'])
    assert engine.observe(candle(6, 10.48, 10.49))['state'] in ('recovery','consolidation')


def test_prefix_immutable_when_future_confirms_swing():
    bars = [candle(i, price, price+.03) for i,price in enumerate([10,10.1,10.2,10.15,10.05,10.2])]
    engine = StructuralDetector()
    prefix = [engine.observe(b) for b in bars[:3]]
    frozen = deepcopy(prefix)
    later = [engine.observe(b) for b in bars[3:]]
    assert prefix==frozen
    fresh = StructuralDetector()
    assert [fresh.observe(b) for b in bars][:3]==prefix
    for row in prefix+later:
        assert all(l['confirmed_at']<=row['effective_at'] for l in row['confirmed_swings'])


def test_global_break_uses_prior_level_even_when_current_book_removes_it():
    engine = StructuralDetector()
    engine.observe(candle(0,10,10.1),[level()], 'available')
    touch = engine.observe(candle(1,10.1,10.21,10.22),[level()], 'available')
    assert not any(e['state']=='breakout' for e in touch['global_events'])
    broken = engine.observe(candle(2,10.21,10.3),[], 'available')
    assert any(e['state']=='breakout' for e in broken['global_events'])
    assert broken['global_context']=='above_broken_resistance'


def test_local_and_global_events_are_separate():
    engine = StructuralDetector()
    engine.observe(candle(0,10,10.1),[level()], 'available')
    row = engine.observe(candle(1,10.1,10.05,10.21),[level()], 'available')
    assert any(e['state']=='rejection' for e in row['global_events'])
    assert not any(e['state']=='rejection' for e in row['local_events'])


def test_future_evidence_rejected():
    engine = StructuralDetector()
    with pytest.raises(ValueError,match='Future'):
        engine.observe(candle(0,10,10.1),[dict(level(),confirmed_at_ms=2000)],'available')


@pytest.mark.parametrize('duration',[.1,1,60,86400])
def test_other_timeframes_and_final_close(duration):
    bars = [candle(i*duration,10,10.1,duration=duration) for i in range(3)]
    request = DetectorRequest(ticker='TEST',timeframe='test',as_of=2*duration,candles=bars)
    result = calculate(request,NoGlobal)
    assert len(result['rows'])==2
    assert result['pending_count']==1
    assert result['rows'][-1]['effective_at']==2*duration


def test_incremental_cache_rewind_and_corrected_prefix():
    bars = [candle(i,10+i*.1,10.1+i*.1) for i in range(5)]
    request = lambda n: DetectorRequest(ticker='TEST',timeframe='1s',as_of=n,candles=bars)
    first = calculate(request(3),NoGlobal)
    full = calculate(request(5),NoGlobal)
    assert full['rows'][:3]==first['rows']
    assert NoGlobal.calls==5
    assert calculate(request(2),NoGlobal)['rows']==full['rows'][:2]
    assert NoGlobal.calls==5
    bars[0]=candle(0,9,9.1)
    corrected = calculate(request(5),NoGlobal)
    assert corrected['rows'][0]['candle']['close']==9.1
    assert NoGlobal.calls==10


def test_recent_body_weight_exceeds_older_body_weight():
    engine = StructuralDetector(DetectorSettings(body_half_life=2))
    engine.observe(candle(0,10,11))
    for i in range(1,10):
        engine.observe(candle(i,11,11.01))
    old = engine.body
    engine.observe(candle(10,11,12))
    assert engine.body > old*2


def test_local_structure_scales_in_candles_and_preserves_real_timestamps():
    prices = [10,10.1,10.2,10.15,10.05,10.2,10.3,10.1,10.4,10.2]
    results = []
    for seconds in [1,60,86400]:
        engine = StructuralDetector()
        rows = [engine.observe(candle(i*seconds,p,p+.02,duration=seconds)) for i,p in enumerate(prices)]
        results.append([(r['state'],[e['state'] for e in r['local_events']]) for r in rows])
        assert all(l['confirmed_at'] <= r['effective_at'] for r in rows for l in r['confirmed_swings'])
    assert results[0]==results[1]==results[2]


def test_split_adjusted_chart_explicitly_excludes_incompatible_v5_basis():
    result=calculate(DetectorRequest(ticker='TEST',timeframe='1d',as_of=2,
        candles=[candle(0,10,10.1)],split_adjusted=True),NoGlobal)
    assert result['rows'][0]['global_status']=='split_adjusted_chart_requires_matching_global_basis'
    assert NoGlobal.calls==0


def test_http_contract_invalid_order_and_no_strategy_required():
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    payload = dict(ticker='TEST',timeframe='1s',as_of=4,candles=[candle(i,10,10.1) for i in range(4)])
    with patch('src.backend.structural_detector_service.GlobalContext', NoGlobal), patch('src.backend.structural_detector_service.calculate', side_effect=lambda r: calculate(r, NoGlobal)):
        response = client.post('/api/indicators/structural-detector',json=payload)
        assert response.status_code==200
        assert len(response.json()['rows'])==4
        payload['candles'].reverse()
        assert client.post('/api/indicators/structural-detector',json=payload).status_code==422


def mirror(bar, center=20):
    return dict(bar,open=center-bar['open'],close=center-bar['close'],
                high=center-bar['low'],low=center-bar['high'])


def test_bullish_and_bearish_movement_are_mirrors():
    bars=[candle(i,o,c) for i,(o,c) in enumerate([(10,10.1),(10.1,10.3),(10.3,10.5),
        (10.5,10.48),(10.48,10.49),(10.49,10.6)])]
    up,down=StructuralDetector(),StructuralDetector()
    pairs={'unknown':'unknown','advance':'decline','pullback':'upward_retracement',
           'recovery':'downward_recovery','consolidation':'consolidation','no_change':'no_change'}
    for bar in bars:
        a,b=up.observe(bar),down.observe(mirror(bar))
        assert pairs[a['state']]==b['state']


def test_retest_lifecycle_is_symmetric_and_does_not_repaint():
    from src.market_engine.structural_evidence import Interactions
    a,b=Interactions(),Interactions()
    resistance=level()
    support=dict(resistance,side=1,lower=20-resistance['upper'],upper=20-resistance['lower'],price=20-resistance['price'])
    bars=[candle(0,10,10.1),candle(1,10.1,10.3),candle(2,10.3,10.4,10.41,10.3),
          candle(3,10.4,10.2,10.4,10.2),candle(4,10.2,10.3,10.31,10.2),candle(5,10.3,10.1)]
    expected=[[],['breakout'],[],['support_retest_unresolved'],['support_retest_held'],['resistance_reclaim']]
    mirrored=[[],['support_failure'],[],['resistance_retest_unresolved'],['resistance_retest_held'],['support_reclaim']]
    previous=None
    saved=[]
    for i,bar in enumerate(bars):
        events,_=a.observe(bar,previous,[resistance] if i<2 else [],.05,i)
        inverse,_=b.observe(mirror(bar),20-previous if previous is not None else None,[support] if i<2 else [],.05,i)
        assert [e['state'] for e in events]==expected[i]
        assert [e['state'] for e in inverse]==mirrored[i]
        if i==2:
            assert a.context()['pending_retests']==b.context()['pending_retests']==1
        saved.append(events)
        if i==1:
            witness=deepcopy(events)
        previous=bar['close']
    assert saved[1]==witness
    assert saved[3][0]['encounters']==2  # break contact, departure, new contact
    assert saved[4][0]['encounters']==2  # consecutive contact is not a new encounter


def test_failed_break_without_retest_and_no_touch_break():
    from src.market_engine.structural_evidence import Interactions
    tracker=Interactions()
    def observe(bar,previous):
        return [e['state'] for e in tracker.observe(bar,previous,[level()],.01,bar['time'])[0]]
    assert observe(candle(0,10.1,10.21,10.3),10.1)==['testing_resistance']
    assert observe(candle(1,10.21,10.3),10.21)==['breakout']
    assert observe(candle(2,10.3,10.1),10.3)==['failed_breakout']


def test_candle_shapes_are_geometry_not_reversal_signals():
    from src.market_engine.structural_evidence import morphology
    bar=candle(1,10.1,10.12,10.4,10.09)
    up=morphology(bar,None,.1,DetectorSettings())
    down=morphology(mirror(bar),None,.1,DetectorSettings())
    assert 'upper_tail' in up['tags'] and 'lower_tail' in down['tags']
    assert up['upper_tail_fraction']==pytest.approx(down['lower_tail_fraction'])
    assert up['close_location']==pytest.approx(1-down['close_location'])
    engine=StructuralDetector()
    engine.observe(candle(0,10,10.1))
    row=engine.observe(bar)
    assert 'upper_tail' in row['candle_shape']['tags']
    assert row['state']!='decline'


def test_bearish_macd_episode_is_context_only():
    engine=StructuralDetector(DetectorSettings(macd_gap_bps=.1))
    rows=[engine.observe(candle(i,10-i*.05,9.96-i*.05)) for i in range(40)]
    assert rows[-1]['macd']['active']
    assert rows[-1]['macd']['direction']==-1
    assert len(rows)==40


def test_global_bias_uses_v5_pivot_time_not_price_sorted_input_order():
    from src.market_engine.structural_evidence import swing_bias
    levels=[dict(level(11,-1),created_at_ms=1000),dict(level(12,-1),created_at_ms=3000),
            dict(level(10,1),created_at_ms=2000),dict(level(10.5,1),created_at_ms=4000)]
    assert swing_bias(levels)=='bullish'
    assert swing_bias(list(reversed(levels)))=='bullish'
    assert swing_bias([level(10),level(11),level(9,1),level(8,1)])=='unknown'


@pytest.mark.parametrize('event,expected',[('support_reclaim','advance'),('resistance_reclaim','decline')])
def test_reclaimed_role_updates_local_movement_direction(event,expected):
    engine=StructuralDetector()
    engine.observe(candle(0,10,10.1))
    close=10.2 if expected=='advance' else 10
    with patch.object(engine.local_interactions,'observe',return_value=([dict(state=event,level=level())],0)):
        assert engine.observe(candle(1,10.1,close))['state']==expected


def test_small_or_unchanged_close_is_not_a_new_pullback_or_recovery():
    engine=StructuralDetector()
    for bar in [candle(0,6.1,6.2),candle(1,6.2,6.5)]:
        engine.observe(bar)
    assert engine.observe(candle(2,6.39,6.4997,6.57,6.39))['state']=='no_change'
    assert engine.observe(candle(3,6.5,6.4997))['state']=='no_change'


def test_small_changes_accumulate_to_meaningful_progress():
    engine=StructuralDetector(DetectorSettings(movement_body_multiple=1,movement_min_bps=10))
    engine.observe(candle(0,10,10.01))
    rows=[engine.observe(candle(i,10.01+(i-1)*.005,10.01+i*.005)) for i in range(1,8)]
    assert any(r['state']=='no_change' for r in rows)
    assert any(r['state']=='advance' for r in rows)


def test_duplicate_band_sources_share_one_encounter_and_persistent_pressure():
    from src.market_engine.structural_evidence import Interactions
    from src.market_engine.structural_progression import Progression
    tracker=Interactions(); settings=DetectorSettings(); progression=Progression(settings)
    band=dict(level(6.75),lower=6.72,upper=6.78)
    sources=[band,dict(band,unified_level_id='another-source')]
    first=candle(0,6.7,6.6,6.88,6.6)
    events,_=tracker.observe(first,6.6,sources,.07,0)
    assert len(events)==1 and len(events[0]['source_ids'])==2
    second=candle(1,6.73,6.67,6.85,6.62)
    events,_=tracker.observe(second,6.6,sources,.07,1)
    assert len(events)==1 and events[0]['encounters']==1 and events[0]['rejection_closes']==2
    progress=progression.observe(second,6.6,1,.07,.007,[],events,'bullish','bearish')
    assert 'persistent_resistance_pressure' in progress['tags']
    assert 'local_global_conflict' in progress['tags']


def test_one_bar_outside_band_does_not_automatically_create_new_encounter():
    from src.market_engine.structural_evidence import Interactions
    tracker=Interactions()
    tracker.observe(candle(0,10.1,10.19,10.2,10.1),10.1,[level()],.05,0)
    tracker.observe(candle(1,10.18,10.18,10.18,10.18),10.19,[level()],.05,1)
    events,_=tracker.observe(candle(2,10.18,10.19,10.2,10.18),10.18,[level()],.05,2)
    assert events[0]['encounters']==1


def test_cycle_memory_failed_attempts_completion_and_comparison():
    from src.market_engine.structural_progression import Progression
    p=Progression(DetectorSettings()); previous=10
    prices=[11,10.5,10.7,10.6,10.8,10.7,11.1,10.9,11.2]
    results=[]
    for i,close in enumerate(prices):
        results.append(p.observe(candle(i,previous,close),previous,1,.1,.005,[],[],'bullish','bullish'))
        previous=close
    assert 'repeated_failed_recovery' in results[5]['tags']
    assert results[5]['cycle']['failed_attempts']==2
    assert results[6]['closed_cycle']['outcome']=='recovered'
    assert results[-1]['completed_cycles']==2
    assert 'improving_cycles' in results[-1]['tags']


def test_impulse_level_progress_and_deep_correction_are_symmetric():
    from src.market_engine.structural_progression import Progression
    up,down=Progression(DetectorSettings()),Progression(DetectorSettings())
    levels=[level(10.2),level(10.3)]
    events=[dict(state='breakout',level=l) for l in levels]
    inverse=[dict(state='support_failure',level=dict(l,side=1,price=20-l['price'],lower=20-l['upper'],upper=20-l['lower'])) for l in levels]
    first=candle(0,10,10.5)
    a=up.observe(first,10,1,.1,.01,[],events,'bullish','bullish')
    b=down.observe(mirror(first),10,-1,.1,.01,[],inverse,'bearish','bearish')
    assert a['crossed']==b['crossed']=={'local':0,'global':2}
    assert 'multiple_levels_crossed' in a['tags']
    second=candle(1,10.5,10.1)
    a=up.observe(second,10.5,1,.1,.01,[],[dict(events[0],state='failed_breakout')],'bullish','bullish')
    b=down.observe(mirror(second),9.5,-1,.1,.01,[],[dict(inverse[0],state='failed_breakdown')],'bearish','bearish')
    assert a['lost']==b['lost']=={'local':0,'global':1}
    assert 'deep_correction' in a['tags'] and set(a['tags'])==set(b['tags'])


def test_progression_decisions_do_not_repaint():
    engine=StructuralDetector()
    bars=[candle(i,p,p+.03) for i,p in enumerate([10,10.3,10.2,10.25,10.15,10.4])]
    prefix=[engine.observe(b) for b in bars[:4]]; frozen=deepcopy(prefix)
    for b in bars[4:]: engine.observe(b)
    assert prefix==frozen


def test_focus_exposes_primary_band_and_counts_without_discarding_events():
    from src.market_engine.structural_evidence import interaction_focus
    events=[dict(state='breakout',level=level(p)) for p in [10.1,10.2,10.3]]
    frozen=deepcopy(events)
    focus=interaction_focus(events,10.4)
    assert focus['primary']['level']['price']==10.3
    assert focus['other_bands']==2 and focus['event_count']==3
    assert events==frozen


def test_direction_change_reports_loss_of_previous_leg_levels():
    from src.market_engine.structural_progression import Progression
    p=Progression(DetectorSettings()); band=level()
    p.observe(candle(0,10,10.4),10,1,.1,.01,[dict(state='breakout',level=band)],[],'bullish','bullish')
    result=p.observe(candle(1,10.4,10),10.4,-1,.1,.01,[dict(state='resistance_reclaim',level=band)],[],'bearish','bullish')
    assert result['lost']['local']==1
    assert 'losing_gained_levels' in result['tags']


def test_retained_band_expiry_is_explicit_and_distant_pending_is_not_current():
    from src.market_engine.structural_evidence import Interactions
    p=Interactions(retention_candles=2)
    p.observe(candle(0,10,10.4),10,[level()],.01,0)
    events,expired=p.observe(candle(1,10.4,10.5),10.4,[],.01,1)
    assert events==[] and expired==0 and p.context()['pending_retests']==1
    events,expired=p.observe(candle(3,10.5,10.6),10.5,[],.01,3)
    assert events==[] and expired==1 and p.context()['tracked_bands']==0
