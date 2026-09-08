#!/usr/bin/env python3
"""Build v5 selections inside ClickHouse from verified v4 candidate checkpoints."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import re
from hashlib import sha256
from time import perf_counter
import prototype_structure_book_clickhouse as P
from build_swing_structure_book import policy
from src.backend.experimental_structure_book import builds
from src.market_engine.resistance_selection_sql import selection_sql
from src.market_engine.swing_book_v5 import VERSION
from src.market_engine.resistance_selection import select_areas


def run(ticker,args):
    start=perf_counter(); folder=args.runtime/ticker.lower(); folder.mkdir(parents=True,exist_ok=True)
    sources=[b for b in builds() if b['ticker']==ticker and b['version']=='causal-swing-closing-book-4' and b['start']<=args.start]
    if not sources: raise ValueError(f'No certified v4 source for {ticker}')
    source=max(sources,key=lambda b:b['end'])
    client=P.Client(args.env_file,args.threads)
    missing=client.query(f"SELECT count() n FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker={P.literal(ticker)} AND source_date BETWEEN '{args.start}' AND '{args.end}' AND source_date<today() AND source_date NOT IN (SELECT session_date FROM {source['id']}.sessions FINAL)",'coverage')
    if int(missing[0]['n']):raise ValueError(f'{ticker}: {missing[0]["n"]} certified days missing from candidate source; extend v4 first')
    if source['end']>args.end:raise ValueError('Requested end precedes source end; select a matching candidate build')
    fingerprint=P.digest([VERSION,source['fingerprint'],selection_sql(source['id']),args.start,args.end,sha256(Path(__file__).read_bytes()).hexdigest()])
    db='structure_book_'+fingerprint[:12]
    policy(client,db)
    client.query(f'CREATE DATABASE IF NOT EXISTS {db}','database',False)
    # Restart-safe INSERTs use deterministic row keys and replacing revisions.
    schema="ticker LowCardinality(String),valid_from_us UInt64,level_id String,lower Float64,upper Float64,price Float64,selection_score Float64,members Array(String),side Int8,revision UInt64"
    client.query(f"CREATE TABLE IF NOT EXISTS {db}.closing ({schema}) ENGINE=ReplacingMergeTree(revision) PARTITION BY cityHash64(ticker)%32 ORDER BY (ticker,valid_from_us,level_id) SETTINGS storage_policy='live_market_ssd'",'schema',False)
    policy(client,db)
    print(f'{ticker}: active=1 queued=0 completed=0 failed=0 | selecting in ClickHouse',flush=True)
    client.query(f"INSERT INTO {db}.closing SELECT *,toInt8(-1),toUInt64(1) FROM ({selection_sql(source['id'])})",'select_resistances',False)
    client.query(f"INSERT INTO {db}.closing SELECT ticker,valid_from_us,concat('s:',toString(level_id)),lower,upper,price,toFloat64(0),[toString(level_id)],toInt8(1),toUInt64(1) FROM {source['id']}.book FINAL WHERE scale='major' AND JSONExtractString(state_json,'state')='active' AND side=1",'supports',False)
    # Close at the immediate next source checkpoint even when an area disappears.
    # This compact public table references the immutable v4 candidate authority.
    client.query(f"CREATE TABLE IF NOT EXISTS {db}.book (ticker LowCardinality(String),level_id String,valid_from_us UInt64,valid_to_us Nullable(UInt64),lower Float64,upper Float64,price Float64,selection_score Float64,side Int8,members Array(String)) ENGINE=ReplacingMergeTree ORDER BY (ticker,level_id,valid_from_us) PARTITION BY cityHash64(ticker)%32 SETTINGS storage_policy='live_market_ssd'",'book_schema',False)
    policy(client,db)
    # Rebuild only this unpromoted deterministic output partition on retry.
    # Replacing keys make deterministic retries safe even after a partial insert.
    client.query(f"""INSERT INTO {db}.book
WITH boundaries AS (SELECT valid_from_us,leadInFrame(toNullable(valid_from_us),1,NULL) OVER (ORDER BY valid_from_us ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS until
 FROM (SELECT DISTINCT valid_from_us FROM (SELECT valid_from_us FROM {source['id']}.book FINAL UNION ALL SELECT toUInt64(closed_at*1000000) AS valid_from_us FROM {source['id']}.sessions FINAL))),
v AS (SELECT c.*,b.until,lagInFrame(tuple(c.lower,c.upper,c.price,c.selection_score,c.side,c.members,b.until),1) OVER (PARTITION BY c.ticker,c.level_id ORDER BY c.valid_from_us ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev FROM {db}.closing AS c FINAL INNER JOIN boundaries b USING(valid_from_us)),
g AS (SELECT *,sum(toUInt64(prev.7 IS NULL OR prev.7!=valid_from_us OR tuple(prev.1,prev.2,prev.3,prev.4,prev.5,prev.6)!=tuple(lower,upper,price,selection_score,side,members))) OVER (PARTITION BY ticker,level_id ORDER BY valid_from_us ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS island FROM v)
SELECT ticker,level_id,min(valid_from_us),if(countIf(until IS NULL)>0,NULL,max(until)),any(lower),any(upper),any(price),any(selection_score),any(side),any(members) FROM g GROUP BY ticker,level_id,island""",'compact_intervals',False)
    policy(client,db)
    counts=client.query(f'SELECT count() rows,countIf(valid_to_us IS NOT NULL AND valid_to_us<=valid_from_us) invalid FROM {db}.book FINAL','verify')
    if int(counts[0]['invalid']):raise ValueError('Invalid v5 intervals')
    stamps=client.query(f'SELECT DISTINCT valid_from_us FROM {source["id"]}.book FINAL ORDER BY valid_from_us','parity_dates')
    chosen=sorted({int(stamps[i]['valid_from_us']) for i in (0,len(stamps)//4,len(stamps)//2,3*len(stamps)//4,len(stamps)-1)}) if stamps else []
    for stamp in chosen:
        raw=client.query(f"SELECT state_json FROM {source['id']}.book FINAL WHERE valid_from_us={stamp}",'parity_input')
        expected={a['id']:a for a in select_areas([json.loads(r['state_json']) for r in raw],stamp/1e6) if a['selected']}
        actual=client.query(f"SELECT * FROM {db}.closing FINAL WHERE valid_from_us={stamp} AND side=-1",'parity_output')
        if set(expected)!={r['level_id'][2:] for r in actual}:raise ValueError('SQL/streaming membership mismatch')
        for row in actual:
            target=expected[row['level_id'][2:]]
            if any(abs(row[k]-target[k])>1e-8 for k in ('lower','upper','price')) or abs(row['selection_score']-target['score'])>1e-8:raise ValueError('SQL/streaming geometry/score mismatch')
    client.query(f"CREATE TABLE IF NOT EXISTS {db}.latest_state (ticker LowCardinality(String),source_book String,closed_at Float64,state_hash String,states Array(String),sequence UInt64) ENGINE=ReplacingMergeTree ORDER BY ticker SETTINGS storage_policy='live_market_ssd'",'state_schema',False)
    policy(client,db)
    client.query(f"INSERT INTO {db}.latest_state SELECT {P.literal(ticker)},{P.literal(source['id'])},any(s.closed_at),any(s.state_hash),groupArray(b.state_json),any(s.sequence) FROM {source['id']}.sessions AS s FINAL INNER JOIN {source['id']}.book AS b FINAL ON b.valid_from_us=toUInt64(s.closed_at*1000000) WHERE s.session_date=(SELECT max(session_date) FROM {source['id']}.sessions FINAL)",'latest_state',False)
    # Drop only the task-owned intermediate projection, not candidate evidence.
    client.query(f'DROP TABLE {db}.closing','drop_staging',False)
    policy(client,db)
    report=dict(version=VERSION,database=db,ticker=ticker,requested_start=args.start,requested_end=args.end,actual_end=source['end'],
        fingerprint=fingerprint,status='built_pending_quality_acceptance',source_book=source['id'],source_fingerprint=source['fingerprint'],
        source_policy=source['source_policy'],runtime=str(folder),elapsed_seconds=perf_counter()-start,counts=counts,
        storage=client.query(f"SELECT sum(bytes_on_disk) bytes FROM system.parts WHERE active AND database='{db}'",'storage'))
    P.save(folder/'report.json',report)
    P.save(folder/'validation.json',dict(status='passed',database=db,checks=['source_coverage','ssd_policy_and_parts','validity_intervals','sql_streaming_parity'],parity_closes=chosen))
    P.save(folder/'profiles.json',client.profiles)
    print(f'{ticker}: completed | {report["elapsed_seconds"]:.3f}s | {counts[0]["rows"]} interval rows | {db}',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tickers',nargs='+',default=['SUGP','JUNS']);p.add_argument('--start',default='2025-01-01');p.add_argument('--end',default=date.today().isoformat())
    p.add_argument('--threads',type=int,default=4);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--env-file',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'))
    a=p.parse_args()
    if not a.runtime.resolve().is_relative_to(Path(r'D:\TradingML\runtimes')) or not Path(r'D:\TradingML\runtimes').is_dir():raise ValueError('Required runtime root unavailable')
    if len(a.tickers)>2 or len(set(a.tickers))!=len(a.tickers) or not 1<=a.threads<=8:raise ValueError('Use 1–2 distinct tickers and 1–8 threads')
    if date.fromisoformat(a.start)>date.fromisoformat(a.end):raise ValueError('Start must precede end')
    if any(not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}',t) for t in a.tickers):raise ValueError('Invalid ticker')
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda t:run(t,a),a.tickers))

if __name__=='__main__':main()
