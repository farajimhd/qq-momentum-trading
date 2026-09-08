from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.market_engine.swing_book import SwingBook, INTRADAY_VERSION
from src.market_engine.swing_book_v5 import StreamingSwingBookV5
from src.market_engine.resistance_selection import select_areas


def engine():
    e=StreamingSwingBookV5(opening=0.)
    e._found({'scale':'major'},(12.,1.,.3),'resistance',2.)
    return e


def test_evidence_change_invalidates_cached_projection_without_geometry_change():
    e=engine()
    e.last_time=3.
    assert not e.snapshot()['unified_levels']
    r=e.active[1]
    r['best_departure']=r['history_threshold']
    revision=e.revision
    e._level_updated(r)
    assert e.revision>revision
    row=e.snapshot()['unified_levels'][0]
    assert row['price']==12. and row['selection_score']==40.
    assert row['p_norm'] is None
    e.snapshot()['unified_levels'].clear()
    assert len(e.snapshot()['unified_levels'])==1


def test_streaming_matches_approved_selector_on_every_observation():
    e=StreamingSwingBookV5(opening=0.)
    for i,p in enumerate([10.,10.3,10.6,10.1,9.8,10.3,10.7,10.,9.9]*15,1):
        e.observe(i,p,p,p)
        expected=[a for a in select_areas(e.active.values(),i) if a['selected']]
        actual=[r for r in e.snapshot()['unified_levels'] if r['side']==-1 and r['lifecycle']=='active']
        assert {a['id'] for a in expected}=={r['unified_level_id'][2:] for r in actual}
        assert all(r['confirmed_at_ms']<=i*1000 for r in actual)


def test_qualified_resistance_survives_break_then_expires_on_role_change():
    e=engine(); e.last_time=3.
    r=e.active[1]
    r['best_departure']=r['history_threshold']; e._level_updated(r)
    original=e.snapshot()['unified_levels'][0]
    r.update(state='awaiting_retest'); e._level_updated(r)
    retained=e.snapshot()['unified_levels'][0]
    assert retained['unified_level_id']==original['unified_level_id']
    assert retained['lower']==original['lower'] and retained['confirmed_at_ms']==original['confirmed_at_ms']
    assert retained['lifecycle']=='awaiting_retest' and retained['retained_qualified_resistance']
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    assert strategy_snapshot(e.snapshot(),datetime.fromtimestamp(4.,timezone.utc))['unified_levels']
    r.update(side='support',state='active'); e._level_updated(r)
    assert not any(row['side']==-1 for row in e.snapshot()['unified_levels'])


def test_unqualified_pending_resistance_is_not_promoted():
    e=engine(); e.active[1]['state']='awaiting_retest'; e._level_updated(e.active[1])
    assert not e.snapshot()['unified_levels']


def test_sparse_and_each_second_readers_have_identical_retained_levels():
    a,b=StreamingSwingBookV5(opening=0.),StreamingSwingBookV5(opening=0.)
    for i,p in enumerate([10.,10.3,10.6,10.1,9.8,10.3,10.7,11.,11.1]*8,1):
        a.observe(i,p,p,p);a.snapshot()
        b.observe(i,p,p,p)
    assert a.snapshot()==b.snapshot()


def test_split_adjusts_new_seed_without_mutating_old_prices():
    e=engine()
    r=e.active[1]
    r['best_departure']=r['history_threshold']
    e._level_updated(r)
    seed=e.closing_state(10.)
    restored=StreamingSwingBookV5(seed,100.,.5)
    assert restored.snapshot()['unified_levels'][0]['price']==6.
    assert seed['levels'][0]['price']==12.


def test_live_consumes_closed_prefix_once_and_never_forming_or_future(monkeypatch):
    from src.backend import live_swing_book_v5 as live
    opening=datetime.fromisoformat('2026-08-21T04:00:00-04:00')
    adapter=object.__new__(live.LiveSwingBookV5)
    adapter.ticker='TEST';adapter.day=opening.date();adapter.cutoff=opening
    adapter.bootstrapped=False
    calls=[]
    adapter.engine=SimpleNamespace(observe=lambda *a:calls.append(a),snapshot=lambda: {'unified_levels':[]})
    def bar(second,closed=True):
        return dict(bar_end=(opening+timedelta(seconds=second)).isoformat(),high=10.,low=9.,close=9.5,is_closed=closed)
    monkeypatch.setattr(live,'qmd_intraday_bar_history',lambda *a,**k:dict(complete=True,bars=[bar(1)],has_more=False))
    monkeypatch.setattr(live,'qmd_bars',lambda *a,**k:dict(ticker='TEST',timeframe='1s',history=[bar(1),bar(2),bar(4)],current=bar(3,False)))
    adapter.snapshot(opening+timedelta(seconds=2.5))
    adapter.snapshot(opening+timedelta(seconds=2.9))
    assert [a[0] for a in calls]==[opening.timestamp()+1,opening.timestamp()+2]
    adapter.snapshot(opening+timedelta(seconds=4))
    assert len(calls)==3
    with pytest.raises(ValueError):adapter.snapshot(opening)


def test_live_incomplete_bootstrap_does_not_advance(monkeypatch):
    from src.backend import live_swing_book_v5 as live
    adapter=object.__new__(live.LiveSwingBookV5)
    adapter.ticker='TEST';adapter.day=datetime(2026,8,21).date()
    monkeypatch.setattr(live,'qmd_intraday_bar_history',lambda *a,**k:dict(complete=False))
    with pytest.raises(ValueError,match='Incomplete'):adapter._history(datetime.fromisoformat('2026-08-21T10:00:00Z'))


def test_strategy_preserves_v5_support_and_resistance_bands_without_pnorm():
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    e=engine();e.last_time=3.
    e.active[1]['best_departure']=e.active[1]['history_threshold'];e._level_updated(e.active[1])
    e._found({'scale':'major'},(11.,1.,.3),'support',2.)
    result=strategy_snapshot(e.snapshot(),datetime.fromtimestamp(3.,timezone.utc),minimum_p_norm=.99)
    assert {r['side'] for r in result['unified_levels']}=={-1,1}
    assert all(r['band_lower']<r['band_upper'] for r in result['unified_levels'])


def test_live_saves_once_only_after_session_close(monkeypatch):
    from src.backend.live_swing_book_v5 import LiveSwingBookV5
    a=object.__new__(LiveSwingBookV5)
    opening=datetime.fromisoformat('2026-08-21T04:00:00-04:00')
    a.day=opening.date();a.cutoff=opening;a.saved=False;a.ticker='TEST';a.source='source'
    a.engine=StreamingSwingBookV5(opening=opening.timestamp())
    written=[]
    a.store=SimpleNamespace(save=lambda *args:written.append(args))
    monkeypatch.setattr(a,'_history',lambda end:[])
    a.finish_session(opening+timedelta(hours=15))
    assert not written
    a.finish_session(opening+timedelta(hours=16))
    a.finish_session(opening+timedelta(hours=17))
    assert len(written)==1 and written[0][2]['closed_at']==opening.timestamp()+16*3600


def test_latest_state_hash_normalizes_clickhouse_numeric_types():
    from hashlib import sha256
    from src.backend.swing_book_v5_store import ClosingStore,encode
    seed=dict(version=INTRADAY_VERSION,closed_at=100.,sequence=5,levels=[])
    store=object.__new__(ClosingStore);store.database='test'
    store.read=lambda sql:[dict(closed_at=100,sequence='5',states=[],state_hash=sha256(encode(seed).encode()).hexdigest())]
    assert store.latest()==seed
