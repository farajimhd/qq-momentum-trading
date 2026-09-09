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
