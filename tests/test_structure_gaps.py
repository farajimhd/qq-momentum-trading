from copy import deepcopy
import pytest
from src.market_engine.structure_gaps import GapAnalyzer, gaps


def level(i, side, lo, hi):
    return dict(unified_level_id=str(i), side=side, lower=lo, upper=hi,
                created_at_ms=0, confirmed_at_ms=0, prominence=1., p_norm=.5)


LEVELS = [level(1,1,5.9,6.), level(2,-1,6.5,6.6), level(3,1,5.5,5.6)]


def test_gap_union_preserves_roles_and_nested_bounds():
    rows=[level(1,1,1,2),level(2,1,1.2,1.5),level(3,1,1.9,2.1),
          level(4,1,3,4),level(5,-1,1.3,1.8),level(6,-1,2,2.5)]
    result=gaps(rows)
    assert [(r['kind'],r['lower'],r['upper']) for r in result]==[
        ('support',2.1,3),('resistance',1.8,2)]


def test_pre_touch_frozen_setup_and_future_outcome():
    engine=GapAnalyzer()
    engine.observe(1,6.1,6.09,6.1,LEVELS,.02)
    engine.observe(2,6.04,6.01,6.02,LEVELS,.02)
    assert len(engine.setups)==1
    frozen=deepcopy(engine.setups[0])
    assert 0<frozen['score']<100
    engine.observe(3,6.03,6.01,6.02,LEVELS,.08)
    assert engine.setups==[frozen]
    engine.observe(4,6.6,6.3,6.5,LEVELS,.08)
    assert engine.setups[0]['outcome']=='target_first'
    assert engine.setups[0]['outcome_at']==4
    assert {k:v for k,v in engine.setups[0].items() if not k.startswith('outcome')}=={
        k:v for k,v in frozen.items() if not k.startswith('outcome')}


@pytest.mark.parametrize('low',[6.,5.95])
def test_already_touched_support_not_predicted(low):
    engine=GapAnalyzer()
    engine.observe(1,6.1,6.09,6.1,LEVELS,.02)
    engine.observe(2,6.04,low,6.02,LEVELS,.02)
    assert not engine.setups


def test_future_levels_rejected_and_newly_confirmed_not_prior_approach():
    engine=GapAnalyzer()
    rows=deepcopy(LEVELS);rows[0]['confirmed_at_ms']=2000
    with pytest.raises(ValueError,match='Future'):engine.observe(1,6.1,6.09,6.1,rows,.02)
    engine.observe(1,6.1,6.09,6.1,LEVELS,.02)
    engine.observe(2,6.04,6.01,6.02,rows,.02)
    assert not engine.setups


def test_same_bar_both_outcomes_ambiguous_and_blocked_room():
    engine=GapAnalyzer()
    engine.observe(1,6.1,6.09,6.1,LEVELS,.02)
    engine.observe(2,6.04,6.01,6.02,LEVELS,.02)
    engine.observe(3,6.6,5.8,6.1,LEVELS,.02)
    assert engine.setups[0]['outcome']=='ambiguous_same_bar'
    blocked=GapAnalyzer()
    rows=LEVELS+[level(4,-1,6.01,6.1)]
    blocked.observe(1,6.1,6.09,6.1,rows,.02)
    blocked.observe(2,6.04,6.01,6.02,rows,.02)
    assert not blocked.setups


def test_prefix_is_identical_before_future_prices_and_levels():
    prefix=[(1,6.1,6.09,6.1),(2,6.04,6.01,6.02)]
    short=GapAnalyzer();full=GapAnalyzer()
    for row in prefix:
        short.observe(*row,LEVELS,.02)
        full.observe(*row,LEVELS,.02)
    full.observe(3,6.6,6.3,6.5,LEVELS+[level(9,1,6.2,6.25)],.03)
    # End times and outcomes become available later; do not backdate them.
    def visible(engine,cutoff):
        setups=[]
        for s in engine.setups:
            if s['time']>cutoff:continue
            s=deepcopy(s)
            if s['outcome_at'] is not None and s['outcome_at']>cutoff:
                s.update(outcome='pending',outcome_at=None)
            setups.append(s)
        return setups
    assert visible(short,2)==visible(full,2)


def test_http_validation_and_busy_release(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.backend import structure_gap_service as service
    app=FastAPI();app.include_router(service.router)
    client=TestClient(app)
    request=dict(ticker='JUNS',session_date='2026-08-21',book_id='structure_book_000000000001')
    assert client.post('/api/research/structure-gaps',json=dict(request,minimum_p_norm=2)).status_code==422
    def fail(_):raise ValueError('Seed is unavailable')
    monkeypatch.setattr(service,'calculate',fail)
    assert client.post('/api/research/structure-gaps',json=request).status_code==422
    monkeypatch.setattr(service,'calculate',lambda _:dict(persisted=False))
    assert client.post('/api/research/structure-gaps',json=request).json()=={'persisted':False}
    service._busy.acquire()
    try:assert client.post('/api/research/structure-gaps',json=request).status_code==429
    finally:service._busy.release()
