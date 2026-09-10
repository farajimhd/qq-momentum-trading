from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from src.backend import swing_book_source as legacy
from src.backend import swing_book_indexed_source as indexed
from scripts import prototype_structure_book_clickhouse as storage
from scripts import swing_reader_upgrade as upgrade
from scripts import build_swing_book_campaign as campaign
from src.market_engine.swing_book_v6 import StreamingSwingBookV6


class SourceClient:
    def __init__(self, session='2025-01-02'):
        left,right=legacy.session_bounds(session)
        self.day=dict(ticker='TEST',source_date=session,event_count=100,next_ordinal=200,last_ordinal=199,
            first_sip_timestamp_us=int(left.timestamp()*1e6),last_sip_timestamp_us=int(right.timestamp()*1e6)-1)
        self.rules=[dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1)]
        self.bars=[dict(t=left.timestamp()+second,high=12.,low=10.,close=11.) for second in (1,7200,7201,14401,57600)]
        self.calls=[]
        self.change_after_read=False

    def query(self, sql, label):
        self.calls.append((sql,label))
        if 'events_ordinal_continuity' in sql:
            return [deepcopy(self.day)]
        if 'event_condition_token_reference' in sql:
            return self.rules
        left=int(re.search(r'e.sip_timestamp_us>=(\d+)',sql)[1])/1e6
        right=int(re.search(r'e.sip_timestamp_us<(\d+)',sql)[1])/1e6
        if self.change_after_read:
            self.day['updated_at']='changed'
        return [dict(row) for row in self.bars if left < row['t'] <= right]


@pytest.mark.parametrize('session',['2025-01-02','2025-03-10','2025-11-03','2025-12-31'])
def test_one_indexed_aggregation_matches_eight_reference_windows(session):
    a,b=SourceClient(session),SourceClient(session)
    assert indexed.read_session('TEST',session,a)==legacy.read_session('TEST',session,b,
        policy=legacy.HISTORICAL_POLICY,query_workers=1)
    queries=[sql for sql,label in a.calls if label=='causal_session_bars']
    assert len(queries)==1
    assert 'e.ordinal>=100 AND e.ordinal<200' in queries[0]
    assert sum('GROUP BY t' in sql for sql,_ in b.calls)==8
    if session=='2025-12-31':
        assert '^events_(2025|2026)$' in queries[0]


def test_sql_optimization_preserves_every_eligibility_and_timestamp_expression():
    c=SourceClient();left,right=legacy.session_bounds('2025-01-02')
    old=legacy.bar_sql('TEST','2025-01-02',left,right,c.rules,policy=legacy.HISTORICAL_POLICY)
    new=indexed.session_sql('TEST','2025-01-02',c.rules,c.day)
    assert new.replace(' AND e.ordinal>=100 AND e.ordinal<200','')==old


@pytest.mark.parametrize('changes',[{'event_count':0},{'next_ordinal':99},{'event_count':300},
    {'first_sip_timestamp_us':999999999999999999}])
def test_invalid_certified_bounds_fail_closed(changes):
    c=SourceClient();c.day.update(changes)
    with pytest.raises(ValueError):indexed.read_session('TEST','2025-01-02',c)
    assert not any(label=='causal_session_bars' for _,label in c.calls)


def test_source_mutation_during_read_is_rejected():
    c=SourceClient();c.change_after_read=True
    with pytest.raises(ValueError,match='changed during aggregation'):
        indexed.read_session('TEST','2025-01-02',c)


@pytest.mark.parametrize('kind',['duplicate','nan','too_many'])
def test_invalid_or_unbounded_ohlc_is_rejected(kind):
    c=SourceClient()
    if kind=='duplicate':c.bars.insert(1,c.bars[0])
    elif kind=='nan':c.bars[0]['close']=float('nan')
    else:c.bars=[c.bars[0]]*(indexed.MAX_SESSION_BARS+1)
    with pytest.raises(ValueError):indexed.read_session('TEST','2025-01-02',c)


def test_http_limit_is_expanded_only_for_bounded_session_aggregation(tmp_path):
    env=tmp_path/'test.env';env.write_text('CLICKHOUSE_URL=http://example.invalid')
    client=storage.Client(env,threads=1)
    urls=[]
    def respond(url,*args):
        urls.append(parse_qs(urlsplit(url).query))
        return b''
    with patch.object(client,'_response',respond):
        client.query('SELECT 1','causal_bars')
        client.query('SELECT 1','causal_session_bars')
    assert [q['max_result_rows'] for q in urls]==[['10000'],['57600']]
    assert all(q['result_overflow_mode']==['throw'] and q['max_result_bytes']==['8000000'] for q in urls)


@pytest.mark.parametrize('separator',['/','\\'])
@pytest.mark.parametrize('legacy_hash',upgrade.LEGACY_BUILDERS)
def test_legacy_build_identity_keeps_old_fingerprint_only_for_known_reader_upgrade(separator,legacy_hash):
    hashes={'scripts/build_swing_structure_book.py':'new','src/market_engine/swing_book_v6.py':'engine',
        **{p:'new' for p in upgrade.UPGRADE_PATHS}}
    old={'scripts/build_swing_structure_book.py':legacy_hash,
        'src/market_engine/swing_book_v6.py':'engine'}
    hashes={k.replace('/',separator):v for k,v in hashes.items()}
    old={k.replace('/',separator):v for k,v in old.items()}
    prior={'code_hash':upgrade.digest(old)}
    assert upgrade.build_identity(hashes,prior,indexed=True)==prior['code_hash']
    with pytest.raises(ValueError):upgrade.build_identity(hashes,prior,indexed=False)
    hashes['src/market_engine/swing_book_v6.py'.replace('/',separator)]='changed'
    with pytest.raises(ValueError):upgrade.build_identity(hashes,prior,indexed=True)


def checkpoint_fixture():
    sessions=['2025-01-02','2025-01-03','2025-01-06']
    bars={};states={};done={};seed=None
    for index,session in enumerate(sessions):
        left,right=legacy.session_bounds(session)
        factor=.5 if index==1 else 1.
        prices=[10.,10.5,11.,10.3,9.,9.5,10.]*8
        if index:prices=[p*.5 for p in prices]
        bars[session]=[(left.timestamp()+i,p,p,p) for i,p in enumerate(prices,1)]
        engine=StreamingSwingBookV6(seed,left.timestamp(),factor)
        for bar in bars[session]:engine.observe(*bar)
        seed=engine.closing_state(right.timestamp());states[int(right.timestamp()*1e6)]=seed
        if index<2:done[session]=dict(closed_at=right.timestamp(),sequence=seed['sequence'],
            state_hash=upgrade.digest(seed),source_revision=json.dumps({'token':session}))
    class Client:
        def query(self,sql,label):
            assert sql.startswith('SELECT ')
            stamp=int(re.search(r'valid_from_us=(\d+)',sql)[1])
            return [dict(state_json=json.dumps(row)) for row in states[stamp]['levels']]
    return sessions,bars,done,Client()


def test_resume_parity_reproduces_last_checkpoint_across_split(monkeypatch):
    sessions,bars,done,client=checkpoint_fixture()
    read=lambda ticker,session,*a,**kw:(bars[session],{'token':session})
    monkeypatch.setattr(upgrade,'legacy_read',read);monkeypatch.setattr(upgrade,'indexed_read',read)
    proof=upgrade.verify('TEST',[dict(source_date=s) for s in sessions],done,client,'test_db',
        [dict(execution_date=sessions[1],split_from=1,split_to=2)])
    assert [s['session'] for s in proof['sessions']]==sessions[1:]
    assert proof['sessions'][0]['state_hash']==done[sessions[1]]['state_hash']


def test_resume_rejects_parity_failure_and_non_prefix_checkpoints(monkeypatch):
    sessions,bars,done,client=checkpoint_fixture()
    monkeypatch.setattr(upgrade,'legacy_read',lambda ticker,session,*a,**kw:(bars[session],{'token':session}))
    monkeypatch.setattr(upgrade,'indexed_read',lambda ticker,session,*a,**kw:([],{'token':session}))
    with pytest.raises(ValueError,match='parity failed'):
        upgrade.verify('TEST',[dict(source_date=s) for s in sessions],done,client,'test_db',[])
    with pytest.raises(ValueError,match='contiguous prefix'):
        upgrade.verify('TEST',[dict(source_date=s) for s in sessions],{sessions[1]:done[sessions[1]]},client,'test_db',[])


def test_campaign_requires_explicit_upgrade_and_pins_new_execution(monkeypatch,tmp_path):
    monkeypatch.setattr(campaign,'ROOT',tmp_path)
    for name in campaign.TRACKED:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'test')
    hashes={p:campaign.sha256((tmp_path/p).read_bytes()).hexdigest() for p in campaign.TRACKED if p not in upgrade.UPGRADE_PATHS}
    hashes['scripts/build_swing_structure_book.py']=upgrade.LEGACY_BUILDERS[0]
    hashes['scripts/build_swing_book_campaign.py']=upgrade.LEGACY_CONTROLLERS[0]
    m={'code_hash':upgrade.digest(hashes)}
    with pytest.raises(ValueError):campaign.reader_for_manifest(m)
    m['reader_upgrade']=dict(execution_code_hash=campaign.code_hash(),prior_code_hash=m['code_hash'],reader='indexed')
    assert campaign.reader_for_manifest(m)=='indexed'
    (tmp_path/'src/market_engine/swing_book_v6.py').write_bytes(b'changed')
    with pytest.raises(ValueError):campaign.reader_for_manifest(m)


def test_builder_verification_failure_prevents_session_writes(monkeypatch,tmp_path):
    import build_swing_structure_book as builder
    import swing_reader_upgrade as gate
    from types import SimpleNamespace
    calls=[]
    class Client:
        profiles=[]
        def query(self,sql,label,**kwargs):
            calls.append(label)
            return [dict(source_date='2025-01-02')] if label=='source_days' else []
        def cancel(self):pass
    monkeypatch.setattr(builder.P,'Client',lambda *a:Client())
    monkeypatch.setattr(builder,'validate_runtime_root',lambda p:p)
    monkeypatch.setattr(builder,'policy',lambda *a:None)
    def fail(*a,**kw):raise ValueError('parity failed')
    monkeypatch.setattr(gate,'verify',fail)
    args=SimpleNamespace(reader='indexed',survivor_only=True,runtime=tmp_path,
        env_file=tmp_path/'unused',threads=1,start='2025-01-01',end='2026-09-04')
    with pytest.raises(ValueError,match='parity failed'):builder.run('TEST',args)
    assert not any(label.startswith('write_') for label in calls)
    assert json.loads((tmp_path/'test'/'report.json').read_text())['status']=='failed'
