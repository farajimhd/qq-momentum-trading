#!/usr/bin/env python3
"""Build compact swing closing books, preserving all existing builds.

Canonical QMD one-second aggregation runs in ClickHouse; only OHLC seconds
reach the shared ordered detector. Restart resumes verified completed days.
"""
import os
import sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as daytime
from hashlib import sha256
import json
import time

import prototype_structure_book_clickhouse as P
from build_structure_book_clickhouse import canonical_splits
from src.backend.swing_book_source import read_session, session_bounds, NY
from src.market_engine.swing_book import VERSION, SwingBook, project


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def policy(client, db):
    found = client.query("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'", 'policy')
    if not found or any(row['disks'] != ['live_market_ssd'] for row in found):
        raise ValueError('Required live_market_ssd policy unavailable')
    if client.query(f"SELECT name FROM system.tables WHERE database='{db}' AND storage_policy!='live_market_ssd'", 'table_policy'):
        raise ValueError('Unexpected table storage policy')
    if client.query(f"SELECT name FROM system.parts WHERE database='{db}' AND active AND disk_name!='live_market_ssd'", 'part_policy'):
        raise ValueError('Existing parts are not on live_market_ssd')


def insert(client, db, table, rows):
    if rows:
        client.query(f'INSERT INTO {db}.{table} FORMAT JSONEachRow\n'+'\n'.join(map(encode, rows)),
                     'write_'+table, read=False)


def split_versions(previous_rows,factor,boundary):
    adjusted = []
    for row in previous_rows:
        state = json.loads(row['state_json'])
        for field in ('price','lower','upper','reversal_distance'):
            if state[field] is not None:
                state[field] *= factor
        adjusted.append(dict(row,price=state['price'],lower=state['lower'],upper=state['upper'],
            valid_from_us=boundary,valid_to_us=None,state_json=encode(state),revision=1))
    return adjusted


def run(ticker, args):
    root = args.runtime.resolve()
    if not root.is_relative_to(Path(r'D:\TradingML\runtimes')):
        raise ValueError('Runtime must be under D:\\TradingML\\runtimes')
    if not Path(r'D:\TradingML\runtimes').is_dir():
        raise ValueError('Required runtime root unavailable')
    folder = root / ticker.lower()
    folder.mkdir(parents=True, exist_ok=True)
    client = P.Client(args.env_file, args.threads)
    started = time.perf_counter()
    code = P.digest({str(p.relative_to(Path(__file__).resolve().parents[1])):sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__).resolve(), Path('src/market_engine/swing_structure.py').resolve(),
                  Path('src/market_engine/swing_book.py').resolve(), Path('src/backend/swing_book_source.py').resolve()]})
    days = client.query(f"SELECT source_date,event_count,next_ordinal,last_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step,updated_at FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker={P.literal(ticker)} AND source_date BETWEEN '{args.start}' AND '{args.end}' ORDER BY source_date", 'source_days')
    if not days:
        raise ValueError(f'No certified days for {ticker}')
    splits = canonical_splits(client.query(f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={P.literal(ticker)} AND execution_date BETWEEN '{args.start}' AND '{args.end}' ORDER BY execution_date", 'splits'))
    coverage = {r['source_date']:r for r in client.query(f"SELECT * FROM q_live.historical_event_execution_clock_coverage_v1 FINAL WHERE ticker={P.literal(ticker)} AND source_date BETWEEN '{args.start}' AND '{args.end}' ORDER BY source_date",'clock_preflight')}
    rules = client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id",'rules')
    fingerprint = P.digest([VERSION,code,ticker,days,splits,coverage,rules])
    db = 'structure_book_'+fingerprint[:12]
    report_path = folder/'report.json'
    previous_report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if previous_report and previous_report['fingerprint'] != fingerprint:
        raise ValueError('Source or code changed: use a new runtime directory')
    report = dict(version=VERSION, database=db, ticker=ticker, fingerprint=fingerprint,
        requested_start=args.start, actual_end=days[-1]['source_date'], status='building',
        threads=args.threads, code_hash=code, runtime=str(folder),
        session_profiles=previous_report.get('session_profiles',[]))
    P.save(report_path, report)
    P.save(folder/'source_manifest.json', [days,rules,splits,coverage])
    missing = [r['source_date'] for r in days if r['source_date'] not in coverage or
        int(coverage[r['source_date']]['event_count'])!=int(r['event_count']) or
        int(coverage[r['source_date']]['clock_count'])!=int(coverage[r['source_date']]['trade_count']) or
        not coverage[r['source_date']]['source_filter_key'].endswith('|delayed_audit_v1')]
    if missing:
        report.update(status='blocked_source_coverage',missing_execution_clock_days=missing)
        P.save(report_path,report)
        raise ValueError(f'{ticker}: missing execution-clock certification for {len(missing)}/{len(days)} days; first={missing[0]}. See {report_path}')
    policy(client,db)
    client.query(f'CREATE DATABASE IF NOT EXISTS {db}', 'database', read=False)
    schemas = {
        'book': "ticker LowCardinality(String),level_id UInt64,scale LowCardinality(String),price Float64,lower Float64,upper Float64,side Int8,prominence Float64,valid_from_us UInt64,valid_to_us Nullable(UInt64),state_json String,revision UInt64",
        'sessions': 'ticker LowCardinality(String),session_date Date,closed_at Float64,sequence UInt64,close Float64,state_hash String,source_revision String,revision UInt64',
        'split_audit': 'ticker LowCardinality(String),effective_us UInt64,price_factor Float64,affected_rows UInt64,before_hash String,after_hash String,source_revision String,revision UInt64',
    }
    keys = {'book':'ticker,valid_from_us,level_id','sessions':'ticker,session_date','split_audit':'ticker,effective_us'}
    for table, schema in schemas.items():
        client.query(f"CREATE TABLE IF NOT EXISTS {db}.{table} ({schema}) ENGINE=ReplacingMergeTree(revision) PARTITION BY cityHash64(ticker)%32 ORDER BY ({keys[table]}) SETTINGS storage_policy='live_market_ssd'", 'schema_'+table, read=False)
    policy(client,db)
    done = {r['session_date']:r for r in client.query(f'SELECT * FROM {db}.sessions FINAL ORDER BY session_date','completed')}
    seed = None
    previous_rows = []
    pending = [d for d in days if d['source_date'] not in done]
    print(f'{ticker} | completed={len(done)} queued={len(pending)} active=0 failed=0 | {db}', flush=True)
    try:
        for index, day in enumerate(days):
            session = day['source_date']
            opening, closing = session_bounds(session)
            if session in done:
                marker = done[session]
                previous_rows = client.query(f"SELECT * FROM {db}.book FINAL WHERE valid_from_us={int(marker['closed_at']*1000000)} ORDER BY level_id", 'resume_rows')
                seed = dict(version=VERSION, closed_at=float(marker['closed_at']),sequence=int(marker['sequence']),levels=[json.loads(r['state_json']) for r in previous_rows])
                if P.digest(seed) != marker['state_hash']:
                    raise ValueError('Persisted closing state hash mismatch')
                continue
            unit = time.perf_counter()
            factor = 1.
            applicable = [s for s in splits if (seed is not None and
                seed['closed_at'] < session_bounds(s['execution_date'])[0].timestamp() <= opening.timestamp())]
            for split in applicable:
                factor *= float(split['split_from'])/float(split['split_to'])
            engine = SwingBook(seed, opening.timestamp(), factor)
            for split in applicable:
                boundary = int(session_bounds(split['execution_date'])[0].timestamp()*1000000)
                ratio = float(split['split_from'])/float(split['split_to'])
                adjusted = split_versions(previous_rows,ratio,boundary)
                for before,after in zip(previous_rows,adjusted):
                    for field in ('price','lower','upper'):
                        if abs(after[field]-before[field]*ratio)>1e-10*max(1.,abs(after[field])):
                            raise ValueError('Split geometry validation failed')
                insert(client,db,'book',[dict(r,valid_to_us=boundary,revision=2) for r in previous_rows])
                insert(client,db,'book',adjusted)
                insert(client,db,'split_audit',[dict(ticker=ticker,effective_us=boundary,price_factor=ratio,
                    affected_rows=len(adjusted),before_hash=P.digest(previous_rows),after_hash=P.digest(adjusted),
                    source_revision=encode(split),revision=1)])
                previous_rows = adjusted
            print(f'{ticker} {session} | active=1 completed={index} queued={len(days)-index-1} failed=0 | aggregating canonical seconds',flush=True)
            bars, revision = read_session(ticker,session,client)
            compute_start = time.perf_counter()
            for bar in bars:
                engine.observe(*bar)
            compute = time.perf_counter()-compute_start
            state = engine.closing_state(closing.timestamp())
            close_time = datetime.combine(date.fromisoformat(session),daytime(16),NY).timestamp()
            regular = [bar for bar in bars if close_time-6.5*3600 < bar[0] <= close_time]
            prior_close = regular[-1][3] if regular else 0.
            boundary = int(closing.timestamp()*1000000)
            closing_rows = [dict(ticker=ticker,level_id=l['level_id'],scale=l['scale'],
                price=l['price'],lower=l['lower'],upper=l['upper'],side=project(l)['side'],
                prominence=project(l)['prominence'],valid_from_us=boundary,valid_to_us=None,
                state_json=encode(l),revision=1) for l in state['levels']]
            insert(client,db,'book',[dict(r,valid_to_us=boundary,revision=2) for r in previous_rows])
            insert(client,db,'book',closing_rows)
            # The marker is last: a restart repeats only deterministic uncommitted writes.
            persisted = client.query(f'SELECT state_json FROM {db}.book FINAL WHERE valid_from_us={boundary} ORDER BY level_id','verify_day')
            check = dict(state,levels=[json.loads(r['state_json']) for r in persisted])
            if P.digest(check)!=P.digest(state):
                raise ValueError('Closing write failed hash verification')
            insert(client,db,'sessions',[dict(ticker=ticker,session_date=session,closed_at=closing.timestamp(),
                sequence=state['sequence'],close=prior_close,state_hash=P.digest(state),
                source_revision=encode(revision),revision=1)])
            seed, previous_rows = state, closing_rows
            report['session_profiles'].append(dict(session=session,bars=len(bars),closing_rows=len(closing_rows),
                compute_seconds=compute,total_seconds=time.perf_counter()-unit))
            P.save(report_path,report)
            print(f'{ticker} {session} | completed={index+1}/{len(days)} active=0 queued={len(days)-index-1} failed=0 | bars={len(bars)} closing={len(closing_rows)} compute={compute:.3f}s total={time.perf_counter()-unit:.2f}s', flush=True)
        policy(client,db)
        intervals = client.query(f"SELECT count() invalid FROM {db}.book FINAL WHERE valid_to_us IS NOT NULL AND valid_to_us<=valid_from_us",'intervals')
        if int(intervals[0]['invalid']):
            raise ValueError('Invalid closing-book validity interval')
        actual_days = client.query(f'SELECT count() n FROM {db}.sessions FINAL','session_count')
        if int(actual_days[0]['n'])!=len(days):
            raise ValueError('Closing session coverage mismatch')
        report.update(status='built_pending_quality_acceptance',elapsed_seconds=time.perf_counter()-started,
            construction_seconds=previous_report.get('construction_seconds',time.perf_counter()-started),
            counts=client.query(f"SELECT count() rows,countIf(scale='major') major_rows FROM {db}.book FINAL",'counts'),
            storage=client.query(f"SELECT sum(rows) physical_rows,sum(bytes_on_disk) bytes FROM system.parts WHERE database='{db}' AND active",'storage'))
        P.save(report_path,report)
        P.save(folder/'validation.json',dict(status='passed',database=db,checks=['daily_state_hash','split_geometry','ssd_policy_and_parts','validity_intervals','session_coverage'],sessions=len(days)))
        print(f'{ticker} complete | {report["counts"]} | {report["storage"]}', flush=True)
    except BaseException as exc:
        report.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error=str(exc))
        P.save(report_path,report)
        client.cancel()
        raise
    finally:
        P.save(folder/f'profiles-{time.time_ns()}.json',client.profiles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tickers',nargs='+',default=['SUGP','JUNS'])
    parser.add_argument('--start',default='2025-01-01')
    parser.add_argument('--end',default=date.today().isoformat())
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--env-file',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'))
    args = parser.parse_args()
    if len(set(args.tickers))!=len(args.tickers) or len(args.tickers)>2 or not 1<=args.threads<=8:
        parser.error('Use at most two unique tickers and 1..8 ClickHouse threads')
    if any(not __import__('re').fullmatch('[A-Z0-9.-]{1,20}',t) for t in args.tickers) or date.fromisoformat(args.start)>date.fromisoformat(args.end):
        parser.error('Invalid ticker or date range')
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='swing-build') as pool:
        list(pool.map(lambda ticker:run(ticker,args),args.tickers))


if __name__=='__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted; completed days are retained for restart.',file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f'Build failed: {exc}',file=sys.stderr)
        raise SystemExit(1)
