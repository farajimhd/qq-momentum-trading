import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.market_engine.swing_book import SwingBook, VERSION, LEGACY_VERSION
from src.market_engine.swing_structure import SwingStructure
from src.backend.swing_book_source import bar_sql, session_bounds, source_metadata
from src.backend.swing_book_source import HISTORICAL_POLICY
from src.backend.swing_book_cursor import SwingBookCursor, SwingChartTimeline


def seed(version=VERSION):
    engine = SwingBook(version=version)
    engine._found({'scale':'major'},(10.,1.,.3),'resistance',2.)
    return engine.closing_state(10.)


def test_compact_kernel_matches_accepted_unseeded_detector():
    a,b = SwingBook(),SwingStructure()
    for i,p in enumerate([10.,10.3,10.6,10.1,9.8,10.3,10.7,10.,9.9],1):
        a.observe(i,p,p,p)
        b.observe(i,p,p,p)
    cleaned = [{k:v for k,v in l.items() if k!='segment'} for l in b.active.values()]
    assert list(a.active.values())==cleaned
    assert a.segments==[]


def test_overnight_pause_preserves_remaining_lifetime_not_a_fresh_lifetime():
    original = seed(LEGACY_VERSION)
    e = SwingBook(original,100000.,version=LEGACY_VERSION)
    assert e.active[1]['last_test']==99992.
    e.observe(100001.,9.,9.,9.)
    assert 1 in e.active
    e.observe(107192.,9.,9.,9.)
    assert 1 not in e.active
    assert original==seed(LEGACY_VERSION)


def test_split_versions_preserve_prior_prices_and_evidence_times():
    original = seed()
    e = SwingBook(original,100000.,.2)
    assert e.active[1]['price']==2.
    assert e.active[1]['lower']==pytest.approx(original['levels'][0]['lower']*.2)
    assert e.active[1]['confirmed_at']==2.
    assert original['levels'][0]['price']==10.
    assert e.sequence==original['sequence']


def test_closing_only_survivors_and_no_visual_evidence():
    e = SwingBook(seed(),100.)
    e.active[1]['state']='awaiting_retest'
    assert e.snapshot()['unified_levels']==[]
    assert len(e.closing_state(101.)['levels'])==1
    e.active[1]['state']='active'
    assert len(e.closing_state(8000.)['levels'])==1
    with pytest.raises(ValueError):
        SwingBook(seed(),0.)


def test_major_survives_months_but_local_expiry_remains():
    e=SwingBook(seed(),100.)
    e._found({'scale':'local'},(8.,101.,.1),'support',102.)
    e.observe(180*86400.,9.,9.,9.)
    assert 1 in e.active
    assert all(l['scale']=='major' for l in e.active.values())
    assert e.snapshot()['unified_levels'][0]['price']==10.


def test_break_hides_anchor_then_next_session_retest_flips_same_id():
    e=SwingBook(seed(),100.)
    e.observe(101.,11.,11.,11.)
    e.observe(102.,11.,11.,11.)
    assert e.snapshot()['unified_levels']==[]
    closed=e.closing_state(103.)
    restored=SwingBook(closed,1000.)
    assert restored.snapshot()['unified_levels']==[]
    restored.observe(1001.,10.01,9.99,10.)
    assert restored.snapshot()['unified_levels']==[]
    restored.observe(1002.,10.5,10.5,10.5)
    level=next(r for r in restored.snapshot()['unified_levels'] if r['unified_level_id']=='1')
    assert level['side']==1 and level['confirmed_at_ms']==1002000
    assert closed['levels'][0]['side']=='resistance'


def test_transient_broken_major_is_not_persisted_and_repeated_pivot_reinforces():
    e=SwingBook()
    e._found({'scale':'major'},(10.,1.,.3),'resistance',2.)
    e._found({'scale':'major'},(10.,3.,.3),'resistance',4.)
    assert len(e.active)==1
    e.observe(5.,11.,11.,11.)
    e.observe(6.,11.,11.,11.)
    assert e.closing_state(7.)['levels']==[]


def test_no_same_bar_confirmation_and_new_ids_follow_carry():
    e = SwingBook(seed(),100.)
    e.observe(101.,12.,9.,10.)
    assert e.sequence==1
    e.observe(102.,9.,9.,9.)
    assert e.sequence>1
    assert all(l['confirmed_at']>l['pivot_at'] for l in e.active.values())


def test_missing_execution_clock_fails_closed():
    with pytest.raises(ValueError,match='Missing certified'):
        source_metadata('JUNS','2026-08-13',lambda sql:[])


def test_historical_policy_uses_certified_sip_without_execution_clock():
    queries = []
    def query(sql):
        queries.append(sql)
        if 'events_ordinal_continuity' in sql:
            return [dict(event_count=10,next_ordinal=20,last_ordinal=19)]
        return [dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1)]
    revision, rules = source_metadata('JUNS','2025-01-02',query,policy=HISTORICAL_POLICY)
    assert revision['source_version'] == HISTORICAL_POLICY
    assert not any('execution_clock' in sql for sql in queries)
    left,right = session_bounds('2025-01-02')
    sql = bar_sql('JUNS','2025-01-02',left,right,rules,policy=HISTORICAL_POLICY)
    assert 'execution_timestamp' not in sql and 'JOIN' not in sql
    assert 'arrayAll' in sql and 'last_ok' in sql and 'high_low_ok' in sql
    assert 'tuple(e.sip_timestamp_us,e.ordinal)' in sql
    with pytest.raises(ValueError,match='Missing certified SIP'):
        source_metadata('JUNS','2025-01-02',lambda sql:[],policy=HISTORICAL_POLICY)


def test_split_book_rows_create_new_interval_without_mutating_old():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from build_swing_structure_book import split_versions,encode
    state = seed()['levels'][0]
    rows = [dict(level_id=1,price=state['price'],lower=state['lower'],upper=state['upper'],
        prominence=1.,valid_from_us=10,valid_to_us=None,state_json=encode(state),revision=1)]
    before = copy.deepcopy(rows)
    after = split_versions(rows,.1,20)
    assert rows==before
    assert after[0]['price']==1.
    assert after[0]['valid_from_us']==20 and rows[0]['valid_from_us']==10
    assert after[0]['prominence']==rows[0]['prominence']


def test_sql_is_causal_native_aggregation_and_cross_year_safe():
    start,end = session_bounds('2025-12-31')
    sql = bar_sql('JUNS','2025-12-31',start+timedelta(hours=14),end,[])
    assert '^events_(2025|2026)$' in sql
    assert 'intDiv(c.execution_timestamp_us,1000000)>=intDiv(e.sip_timestamp_us,1000000)' in sql
    assert 'argMaxIf' in sql and 'GROUP BY t' in sql
    assert 'file(' not in sql


def fixture_inputs(*args):
    opening,_ = session_bounds('2026-08-21')
    t = opening.timestamp()
    state = seed()
    state['closed_at']=t-36000
    state['levels'][0]['last_test']=t-36001
    return state,10.,1.,[(t+1,10.,10.,10.),(t+2,11.,11.,11.),(t+3,10.,10.,10.)]


def test_cursor_never_exposes_future_bars_and_rewinds_exactly():
    build = dict(id='structure_book_123456789abc',ticker='JUNS',fingerprint='x',version=VERSION)
    with patch('src.backend.experimental_structure_book.resolve',return_value=build),patch('src.backend.swing_book_cursor.inputs',side_effect=fixture_inputs):
        e = SwingBookCursor(build['id'],'JUNS')
        opening,_ = session_bounds('2026-08-21')
        before = copy.deepcopy(e.snapshot(opening))
        assert e.index==0
        e.snapshot(opening+timedelta(seconds=3))
        assert e.index==3
        assert e.snapshot(opening)==before
        assert e.index==0


def test_chart_pagination_reuses_prefix_and_matches_cursor():
    build = dict(id='structure_book_123456789abc',ticker='JUNS',fingerprint='x',version=VERSION)
    with patch('src.backend.experimental_structure_book.resolve',return_value=build),patch('src.backend.swing_book_cursor.inputs',side_effect=fixture_inputs):
        opening,_ = session_bounds('2026-08-21')
        chart = SwingChartTimeline(build['id'],'JUNS',opening,'x')
        full = chart.rows(opening+timedelta(seconds=3))
        transitions = chart.transitions
        assert chart.rows(opening+timedelta(seconds=3))==full
        assert chart.transitions==transitions
        assert chart.rows(opening)==full[:1]
        assert chart.rows(opening+timedelta(seconds=3),after=opening)==full[1:]


def test_strategy_contract_accepts_new_version_but_rejects_unscored_levels():
    from src.market_engine.swing_book import project
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    from src.trading_runtime.normalized_level_book import CONTRACT
    row = dict(project(seed()['levels'][0]),p_norm=.9,load_contract=CONTRACT)
    at = datetime.fromtimestamp(100,timezone.utc)
    result = strategy_snapshot({'unified_levels':[row]},at,.8)
    assert len(result['unified_levels'])==1
    assert result['unified_levels'][0]['band_upper']==row['upper']
    assert strategy_snapshot({'unified_levels':[dict(row,p_norm=None)]},at,0)['unified_levels']==[]
