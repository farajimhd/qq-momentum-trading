from copy import deepcopy
import json

import pytest

from src.market_engine.structural_detector import StructuralDetector
from src.market_engine.structural_detector_checkpoint import checkpoint, restore
from src.market_engine.structural_evidence import Interactions
from src.market_engine.structural_labels import level_context


def bar(t, opened, close, low=None, high=None):
    return dict(time=t,end=t+1,open=opened,close=close,low=min(opened,close)-.01 if low is None else low,
                high=max(opened,close)+.01 if high is None else high,volume=1000)


def level(side=-1):
    return dict(unified_level_id='a',side=side,lower=9.99,upper=10.01,price=10.,confirmed_at_ms=0,
                book_version='causal-swing-closing-book-6',scale='major',selection_score=40.)


Q = dict(atr=.1,ready=True,penetration_atr=.1,body_atr=.3,body_fraction=.4,acceptance_closes=2)


def states(events):
    return [e['state'] for e in events]


@pytest.mark.parametrize('sign',[1,-1])
def test_tiny_cross_cannot_break_but_sustained_departure_can(sign):
    engine=Interactions()
    def observe(t,o,c):
        b=bar(t,10+sign*(o-10),10+sign*(c-10))
        return engine.observe(b,10+sign*(o-10),[level(-sign)],.1,t,Q)[0]
    first=observe(1,10.009,10.012)
    assert states(first)==['resistance_cross' if sign==1 else 'support_cross']
    assert first[0]['phase']=='active'
    assert 'shallow_penetration' in first[0]['qualification']['reasons']
    later=observe(2,10.012,10.05)
    assert states(later)==['breakout' if sign==1 else 'support_failure']
    assert later[0]['qualification']['accepted'] is False


def test_retest_requires_later_contact_and_departure_acceptance_is_separate():
    engine=Interactions()
    events,_=engine.observe(bar(1,9.95,10.08),9.95,[level()],.1,1,Q)
    assert states(events)==['breakout']
    events,_=engine.observe(bar(2,10.08,10.04,9.995,10.09),10.08,[level()],.1,2,Q)
    assert states(events)==['support_retest_unresolved']
    events,_=engine.observe(bar(3,10.04,10.07,10.03,10.08),10.04,[level()],.1,3,Q)
    assert states(events)==['support_retest_held']
    assert events[0]['qualification']['accepted']


def test_acceptance_requires_two_later_closes_and_failure_hysteresis():
    engine=Interactions()
    engine.observe(bar(1,9.95,10.08),9.95,[level()],.1,1,Q)
    first,_=engine.observe(bar(2,10.08,10.09),10.08,[level()],.1,2,Q)
    assert 'breakout_accepted' not in states(first)
    accepted,_=engine.observe(bar(3,10.09,10.1),10.09,[level()],.1,3,Q)
    assert states(accepted)==['breakout_accepted']
    tiny,_=engine.observe(bar(4,10.1,9.985),10.1,[level()],.1,4,Q)
    assert 'failed_breakout' not in states(tiny)
    failed,_=engine.observe(bar(5,9.985,9.95),9.985,[level()],.1,5,Q)
    assert states(failed)==['failed_breakout']


def test_prior_atr_immutable_prefix_and_checkpoint_continuation():
    engine=StructuralDetector()
    bars=[bar(i,9.8,9.8,9.75,9.85) for i in range(6)]+[bar(6,9.8,10.3)]
    prefix=[engine.observe(b,[level()],'available') for b in bars[:-1]]
    frozen=deepcopy(prefix)
    resumed=restore(json.loads(json.dumps(checkpoint(engine))))
    result=engine.observe(bars[-1],[level()],'available')
    assert result==resumed.observe(bars[-1],[level()],'available')
    assert result['qualification']['atr']==pytest.approx(.1)
    assert 'breakout' in states(result['global_events'])
    assert prefix==frozen
    assert set(result['labels'])=={'movement','regime','geometry','displacement','interaction','break_lifecycle',
        'retest_lifecycle','structural_progression','correction_recovery','pressure','volume','reversal','evidence'}
    assert result['summary']['label']=='breakout'


def test_warmup_and_gap_do_not_create_qualified_break():
    engine=StructuralDetector()
    engine.observe(bar(1,9.8,9.9),[level()],'available')
    row=engine.observe(bar(2,9.9,10.2),[level()],'available')
    assert states(row['global_events'])==['resistance_cross']
    assert not row['qualification']['ready']
    row=engine.observe(bar(10,10.2,10.3),[level()],'available')
    assert row['gap_before'] and row['sequence']==1
    assert row['qualification']['observations']==0


def test_location_and_score_units_are_separate():
    resistance=level()
    support=dict(level(1),price=9.5,lower=9.49,upper=9.51)
    context=level_context(resistance,[support,resistance],9.8,.1)
    assert context['location']=='outer'
    assert context['prominence_atr'] is None
    assert context['selection_score']==40
    assert level_context(resistance,[resistance],9.8,.1)['location']=='unknown'


def test_minor_level_cross_and_same_bar_confirmation_cannot_qualify():
    for l in (dict(level(),reversal_distance=.01),dict(level(),confirmed_at_ms=2000)):
        events,_=Interactions().observe(bar(1,9.95,10.08),9.95,[l],.1,1,Q)
        assert states(events)==['resistance_cross']


def test_default_context_chooses_only_matching_v6_books():
    from unittest.mock import patch
    from src.backend.structural_detector_service import GlobalContext
    def book(ticker,version,identity):
        return dict(ticker=ticker,version='causal-swing-closing-book-'+str(version),id=identity,end='2026-09-04')
    with patch('src.backend.experimental_structure_book.builds',return_value=[book('SUGP',5,'old'),book('JUNS',6,'other'),book('SUGP',6,'correct')]):
        context=GlobalContext('SUGP',None)
        assert [b['id'] for b in context.books]==['correct']


def test_chart_api_and_strategy_consume_identical_contiguous_candle_labels():
    from src.backend.structural_detector_service import DetectorRequest,calculate,_cache
    from src.trading_runtime.structural_recovery import observe_market
    book=dict(version='causal-swing-closing-book-6',id='test',fingerprint='pinned')
    class Context:
        def __init__(self,*args):
            self.book=book
        def at(self,bar):
            return [level()],'available'
    bars=[bar(i,9.8,9.8,9.75,9.85) for i in range(6)]+[bar(6,9.8,10.3),bar(7,10.3,10.4)]
    _cache.clear()
    rows=calculate(DetectorRequest(ticker='TEST',timeframe='1s',as_of=8,candles=bars),Context)['rows']
    saved={}
    for candle,row in zip(bars,rows):
        saved=observe_market(candle,[level()],book,saved)
        assert saved['row']==row
    _cache.clear()
