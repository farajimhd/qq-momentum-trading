from copy import deepcopy

import pytest

from src.market_engine.swing_book import SwingBook, QUALIFIED_VERSION, INTRADAY_VERSION


def book(side='support'):
    engine = SwingBook(version=QUALIFIED_VERSION)
    engine._found({'scale':'major'}, (10.,1.,.15), side,2.)
    return engine


@pytest.mark.parametrize('side,price',[('support',10.4),('resistance',9.6)])
def test_strong_reaction_qualifies_and_survives_untouched(side,price):
    e=book(side)
    e.observe(3.,price,price,price)
    state=e.closing_state(4.)
    assert any(l['level_id']==1 for l in state['levels'])
    next_day=SwingBook(state,100000.,version=QUALIFIED_VERSION)
    next_day.observe(100001.,price,price,price)
    assert any(l['level_id']==1 for l in next_day.closing_state(100002.)['levels'])


def test_weak_reaction_is_visible_today_but_not_carried():
    e=book()
    e.observe(3.,10.15,10.15,10.15)
    assert e.snapshot()['unified_levels']
    assert not any(l['level_id']==1 for l in e.closing_state(4.)['levels'])


def test_two_independent_material_retests_qualify():
    e=book()
    for t,p in enumerate([10.15,10.,10.15,10.,10.15],3):
        e.observe(t,p,p,p)
    assert e.active[1]['independent_retests']==2
    assert any(l['level_id']==1 for l in e.closing_state(9.)['levels'])


def test_adjacent_touch_bars_do_not_count_as_independent_retests():
    e=book()
    for t in range(3,12):
        e.observe(t,10.15,10.,10.15)
    assert e.active[1]['independent_retests']==0
    assert not any(l['level_id']==1 for l in e.closing_state(12.)['levels'])


def test_two_accepted_crossings_retire_without_time_expiry():
    e=book()
    e.observe(3.,10.4,10.4,10.4)
    for t,p in enumerate([9.8,9.8,10.05,9.8,9.8],4):
        e.observe(t,p,p,p)
    assert 1 not in e.active and 1 not in e.level_index.records
    assert e.counts['events_history_retired']==1


def test_material_retest_resets_crossing_weakness():
    e=book()
    for t,p in enumerate([10.4,9.8,9.8,10.15,10.,10.15],3):
        e.observe(t,p,p,p)
    assert e.active[1]['independent_retests']==1
    assert e.active[1]['accepted_crossings']==0


def test_split_scales_qualification_distances_without_rewriting_seed():
    e=book();e.observe(3.,10.4,10.4,10.4)
    seed=e.closing_state(4.);before=deepcopy(seed)
    adjusted=SwingBook(seed,100.,5.,version=QUALIFIED_VERSION)
    for field in ('history_threshold','retest_threshold','best_departure'):
        assert adjusted.active[1][field]==pytest.approx(seed['levels'][0][field]*5)
    assert seed==before


def test_later_session_range_cannot_rewrite_earlier_snapshot():
    e=book('resistance');e.observe(3.,9.65,9.65,9.65)
    before=deepcopy(e.snapshot())
    e.observe(4.,20.,20.,20.)
    assert not e._qualified_for_carry(e.active[1])
    assert before['unified_levels'][0]['confirmed_at_ms']==2000


@pytest.mark.parametrize('version',[QUALIFIED_VERSION,INTRADAY_VERSION])
def test_qualified_index_matches_full_scan_with_gaps_and_session_carry(version):
    from random import Random
    class Scanned(SwingBook):
        def _levels_to_update(self,t,high,low,close,tick):
            self.history_bar=(t,high,low,close)
            return list(self.active)
    engines=[SwingBook(version=version),Scanned(version=version)]
    rng=Random(91);price=10.;t=10.
    for e in engines:
        for j in range(30):
            e._found({'scale':'major'},(8+j*.15,1.,.15),'support' if j%2 else 'resistance',2.)
    for i in range(1000):
        if i==500:
            seeds=[e.closing_state(t) for e in engines]
            assert seeds[0]==seeds[1]
            t+=86400
            engines=[SwingBook(seeds[0],t,2.,version=version),Scanned(seeds[1],t,2.,version=version)]
            price*=2
        t+=1;price=max(.1,price+rng.uniform(-.3,.3))
        if i%97==0: price=rng.uniform(8,14)
        high,low=price+.1,price-.05
        for e in engines: e.observe(t,high,low,price)
        if engines[0].active != engines[1].active:
            differences={k:{f:(v,engines[1].active.get(k,{}).get(f)) for f,v in row.items()
                            if v!=engines[1].active.get(k,{}).get(f)}
                         for k,row in engines[0].active.items() if row!=engines[1].active.get(k)}
            raise AssertionError((i,differences))
        assert engines[0].counts==engines[1].counts
