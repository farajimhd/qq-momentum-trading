from copy import deepcopy
from threading import RLock
import json
import pytest

from src.market_engine.structural_thesis import observe as context, pressure
from src.market_engine.structural_signal import observe
from src.market_engine.structural_detector import StructuralDetector, DetectorSettings
from src.market_engine.structural_detector_checkpoint import checkpoint, restore
from src.backend.swing_book_cursor import SwingBookCursor
from test_structural_signal import row, band, levels


def test_broader_context_uses_only_closed_buckets_and_is_prefix_stable():
    state={};outputs=[]
    for i in range(90):
        outputs.append(context(state,row(i,close=10+i*.01)))
    assert outputs[-2]['completed_buckets']==2
    assert outputs[-1]['completed_buckets']==3
    assert outputs[-1]['direction']==1
    frozen=deepcopy(outputs)
    future=context(state,row(90,close=5))
    assert future['direction']==1  # Unclosed bucket cannot rewrite the trend.
    assert future['context_through']==90
    assert outputs==frozen


@pytest.mark.parametrize('sign',[1,-1])
def test_pressure_requires_repeated_tests_advancing_base_and_volume(sign):
    bars=[]
    for i in range(8):
        p=9.7+i*.03
        b=dict(time=i,end=i+1,open=p-.02,close=p,high=p+.08,low=p-.05,volume=100)
        if sign==-1:
            b.update(open=20-b['open'],close=20-b['close'],high=20-(p-.05),low=20-(p+.08))
        bars.append(b)
    c=dict(fast_bars=bars,direction=sign,fast_direction=sign)
    trigger=dict(lower=9.95,upper=10.05)
    assert pressure(c,sign,trigger,.1)
    bad=deepcopy(c);bad['fast_bars'][-1]['volume']=None
    assert not pressure(bad,sign,trigger,.1)
    assert not pressure(dict(c,direction=-sign),sign,trigger,.1)


def test_qualified_impulse_can_enter_without_oscillator_warmup():
    r=row(30,'breakout');r['momentum']['agreement']='warming_up'
    r['candle']['open']=9.99
    result=observe({},r,levels(),DetectorSettings())
    assert result['action']=='long_enter'
    assert result['setup']['entry_mode']=='structural_impulse'
    assert result['setup']['thesis']['path']


def test_anticipatory_entry_records_uncleared_challenge_and_conditional_room():
    state={}
    for i in range(7):
        context(state.setdefault('context',{}),row(82+i,close=9.7+i*.03))
    r=row(89,close=9.91);r['momentum']['agreement']='warming_up'
    result=observe(state,r,[band(9.8,1),band(10),band(11)],DetectorSettings())
    assert result['action']=='long_enter'
    p=result['setup']
    assert p['pattern']=='anticipation'
    assert p['thesis']['trigger']['lower']>r['candle']['close']
    assert p['room_basis']=='conditional_on_trigger_acceptance'
    assert p['target']>p['barrier']['upper']


def test_repeated_rejection_sequence_is_not_pressure():
    bars=[row(i,close=9.7+i*.03)['candle'] for i in range(8)]
    c=dict(fast_bars=bars,direction=1,fast_direction=1,evidence=[
        dict(state='rejection',level=band(10)),dict(state='failed_breakout',level=band(10))])
    assert not pressure(c,1,band(10),.1)


def test_a_retained_breakout_does_not_become_a_late_entry():
    state={};r=row(30,'breakout',volume=0)
    assert observe(state,r,levels(),DetectorSettings())['action']=='wait'
    later=row(31,'breakout')
    # Same event and same close, now with volume: the original trigger is stale.
    assert observe(state,later,levels(),DetectorSettings())['action']=='wait'


def test_pre_entry_rejection_is_not_a_new_position_exit():
    state={};entry=row(30,'breakout')
    old=dict(state='failed_breakout',level=band(11),break_at=20,encounters=1)
    entry['global_events'].append(old)
    assert observe(state,entry,levels(),DetectorSettings())['action']=='long_enter'
    held=row(31);held['global_events']=[old]
    assert observe(state,held,levels(),DetectorSettings())['action']=='long_hold'


def test_certified_empty_interval_preserves_checkpoint_but_unknown_gap_resets():
    engine=StructuralDetector()
    for i in range(10):engine.observe(row(i)['candle'])
    recovered=restore(json.loads(json.dumps(checkpoint(engine))))
    proof=dict(contract='canonical-empty-interval-1',start=10,end=12,fingerprint='certified')
    next_bar=row(12)['candle']
    result=engine.observe(next_bar,continuity=proof)
    assert result==recovered.observe(next_bar,continuity=proof)
    assert result['continuity']['status']=='certified_empty_interval'
    assert result['sequence']==11
    assert not result['gap_before']
    assert engine.observe(row(14)['candle'])['gap_before']


def test_continuity_certificate_rejects_omitted_bar_future_and_long_gap():
    cursor=object.__new__(SwingBookCursor)
    cursor.lock=RLock();cursor.at=20;cursor.session='1969-12-31'
    cursor.build=dict(id='certified',fingerprint='revision')
    cursor.bars=[(10,1,1,1),(12,1,1,1),(20,1,1,1)]
    assert cursor.empty_interval(10,11)
    assert cursor.empty_interval(10,12) is None
    assert cursor.empty_interval(20,21) is None
    assert cursor.empty_interval(-20,11) is None


def test_trend_position_holds_ordinary_pullback_but_exits_base_failure():
    state={};s=DetectorSettings()
    # Populate broad context without feeding a hypothetical entry.
    for i in range(90):context(state.setdefault('context',{}),row(i,close=9+i*.012))
    entry=observe(state,row(90,'breakout'),levels(),s)
    assert entry['action']=='long_enter'
    pullback=row(91,close=10.19);pullback['momentum']['agreement']='mixed'
    pullback['candle'].update(open=10.2,low=10.18,high=10.21)
    held=observe(state,pullback,levels(),s)
    assert held['action']=='long_hold'
    assert held['setup']['assessment']['base_intact']
    failed=row(92,'support_failure',close=10.18)
    failed['candle'].update(low=10.17,high=10.2)
    assert observe(state,failed,levels(),s)['reason']=='entry_structure_failed'
