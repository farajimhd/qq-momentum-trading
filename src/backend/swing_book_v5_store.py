"""Compact end-of-session v5 persistence; candidate state is one row per ticker."""
from hashlib import sha256
from math import ulp
import json
import re

from research.mlops.clickhouse import ClickHouseHttpClient,default_clickhouse_url,default_clickhouse_user,default_clickhouse_password
from src.market_engine.swing_book_v5 import StreamingSwingBookV5, CONTRACT, LEGACY_CONTRACT


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


class ClosingStore:
    def __init__(self, database, *, contract=CONTRACT):
        if not re.fullmatch(r'structure_book_[a-f0-9]{12}',database):raise ValueError('Invalid v5 database')
        self.database=database
        if contract not in (CONTRACT,LEGACY_CONTRACT):raise ValueError('Unsupported V5 selection contract')
        self.contract=contract
        self.client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password(),
            timeout_seconds=30,default_query_params={'max_threads':2,'max_memory_usage':536870912})

    def read(self,sql):
        return [json.loads(r) for r in self.client.execute(sql+' FORMAT JSONEachRow').splitlines() if r]

    def verify_storage(self):
        db=self.database
        policies=self.read("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
        if not policies or any(r['disks']!=['live_market_ssd'] for r in policies):raise ValueError('Required SSD policy unavailable')
        if self.read(f"SELECT name FROM system.tables WHERE database='{db}' AND storage_policy!='live_market_ssd'"):
            raise ValueError('V5 tables must use live_market_ssd')
        if self.read(f"SELECT name FROM system.parts WHERE active AND database='{db}' AND disk_name!='live_market_ssd'"):
            raise ValueError('V5 parts must reside on live_market_ssd')

    def latest(self):
        result=self.read(f'SELECT * FROM {self.database}.latest_state FINAL')
        if len(result)>1:raise ValueError('V5 database must contain one ticker')
        if not result:return None
        r=result[0]
        if not r.get('sequence'):return None  # Historical source marker supplies legacy sequence.
        seed=dict(version='causal-swing-closing-book-4',closed_at=float(r['closed_at']),sequence=int(r['sequence']),
                  levels=sorted([json.loads(s) for s in r['states']],key=lambda l:l['level_id']))
        if sha256(encode(seed).encode()).hexdigest()!=r['state_hash']:raise ValueError('V5 latest-state hash mismatch')
        return seed

    def save(self,ticker,source_book,seed):
        self.verify_storage()
        db=self.database;stamp=int(seed['closed_at']*1e6)
        current=self.latest()
        if current and current['closed_at']>seed['closed_at']:raise ValueError('Cannot overwrite newer closing state')
        contract=getattr(self,'contract',LEGACY_CONTRACT)
        visible=StreamingSwingBookV5(seed,seed['closed_at'],contract=contract).snapshot()['unified_levels']
        next_rows={}
        for r in visible:
            key=r['unified_level_id'] if r['side']==-1 or contract==CONTRACT else 's:'+r['unified_level_id']
            # Stable member ids are present in the resistance area identifier;
            # retain them explicitly for SQL/streaming verification.
            members=r.get('selection_members',[r['unified_level_id']])
            next_rows[key]=dict(ticker=ticker,level_id=key,valid_from_us=stamp,valid_to_us=None,
                lower=r['lower'],upper=r['upper'],price=r['price'],selection_score=r.get('selection_score') or 0.,side=r['side'],members=members)
        prior={r['level_id']:r for r in self.read(f'SELECT * FROM {db}.book FINAL WHERE valid_to_us IS NULL')}
        changes=[];fields=('lower','upper','price','selection_score','side','members')
        for key,r in prior.items():
            following=next_rows.get(key)
            def equal(field):
                a,b=r[field],following[field]
                return abs(a-b)<=8*max(ulp(float(a)),ulp(float(b))) if field in ('lower','upper','price','selection_score') else a==b
            if following and all(equal(f) for f in fields):next_rows.pop(key)
            else:
                if r['valid_from_us']>=stamp:raise ValueError('Conflicting same-time closing state')
                changes.append(dict(r,valid_to_us=stamp))
        changes.extend(next_rows.values())
        if changes:self.client.execute(f'INSERT INTO {db}.book FORMAT JSONEachRow\n'+'\n'.join(map(encode,changes)))
        record=dict(ticker=ticker,source_book=source_book,closed_at=seed['closed_at'],sequence=seed['sequence'],
            state_hash=sha256(encode(seed).encode()).hexdigest(),states=[encode(r) for r in seed['levels']])
        self.client.execute(f'INSERT INTO {db}.latest_state FORMAT JSONEachRow\n'+encode(record))
        self.verify_storage()
        if self.latest()!=seed:raise ValueError('Closing state readback mismatch')
