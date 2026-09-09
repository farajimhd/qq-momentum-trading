#!/usr/bin/env python3
"""Plan, run, inspect or gracefully stop a workstation V6 daily-survivor build."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse
from contextlib import contextmanager
from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
import subprocess
import time
from types import SimpleNamespace

import prototype_structure_book_clickhouse as P
from build_swing_structure_book import policy

RUNTIME=Path(r'D:\TradingML\runtimes')
MAX_WORKERS=64
MAX_QUERY_THREADS=128


def validate_concurrency(workers, threads):
    if not 1<=workers<=MAX_WORKERS or not 1<=threads<=8 or workers*threads>MAX_QUERY_THREADS:
        raise ValueError('Use 1..64 workers, 1..8 threads; combined query thread budget <=128')


TRACKED=('scripts/build_swing_book_campaign.py','scripts/build_swing_structure_book.py',
 'src/market_engine/swing_book_v6.py','src/market_engine/swing_book.py',
 'src/market_engine/swing_structure.py','src/market_engine/swing_level_index.py',
 'src/market_engine/swing_book_v5.py','src/market_engine/resistance_selection.py',
 'src/backend/swing_book_source.py')


def code_hash():
    return P.digest({p:sha256((ROOT/p).read_bytes()).hexdigest() for p in TRACKED})


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive(path):
    with path.open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def save(root, manifest):
    manifest['updated_at']=now()
    P.save(root/'manifest.json',manifest)


def duration(seconds):
    return 'unavailable' if seconds is None else f'{seconds/60:.1f} min'


def summary(m):
    counts=Counter(r['status'] for r in m['rows'])
    completed=[r for r in m['rows'] if r['status']=='completed' and r.get('elapsed_seconds') is not None]
    remaining=counts['queued']+counts['active']
    estimate=(sum(r['elapsed_seconds'] for r in completed)/len(completed)*remaining/m['workers']) if completed else None
    return dict(counts=dict(counts),total=len(m['rows']),eta_seconds=estimate,
                eta_basis='mean completed ticker time / worker count; ticker liquidity varies')


def show(m):
    s=summary(m)
    counts=' '.join(f'{k}={s["counts"].get(k,0)}' for k in ('active','queued','completed','deferred','failed','interrupted'))
    print(f'{counts} | ETA {duration(s["eta_seconds"])}',flush=True)
    for row in m['rows']:
        if row['status']=='active':
            p=Path(row['progress_file'])
            progress=json.loads(p.read_text()) if p.exists() else {}
            detail=''
            candidate=Path(row['report'])
            if progress.get('stage')=='v6' and candidate.exists():
                report=json.loads(candidate.read_text());profiles=report.get('session_profiles',[])
                elapsed=sum(x['total_seconds'] for x in profiles)
                estimate=elapsed/len(profiles)*max(0,row['days']-len(profiles)) if profiles else None
                detail=f' days={len(profiles)}/{row["days"]} stage ETA={duration(estimate)}'
            print(f'  {row["ticker"]}: {progress.get("stage","starting")}{detail} | elapsed {duration(time.time()-row["started_epoch"])}',flush=True)


def plan(args):
    root=args.runtime
    if (root/'manifest.json').exists():raise ValueError('Plan exists; use run to resume or choose a new runtime')
    client=P.Client(args.env_file,args.threads)
    policy(client,'structure_book_campaign_preflight')
    stamp=client.query('SELECT toString(max(universe_date)) day FROM q_live.feature_tradable_universe_v1 FINAL','universe_date')[0]['day']
    # Freeze the current published tradable membership; do not infer broker identity.
    universe=[];after=''
    while True:
        page=client.query(f"SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,massive_ticker,source_run_id FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={P.literal(stamp)} AND is_tradable=1 AND ticker>{P.literal(after)} ORDER BY ticker,symbol_id LIMIT 500",'universe_page')
        if not page:break
        # Read a complete final ticker group before advancing the cursor.
        last=page[-1]['ticker']
        page=[r for r in page if r['ticker']!=last]+client.query(f"SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,massive_ticker,source_run_id FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={P.literal(stamp)} AND is_tradable=1 AND ticker={P.literal(last)}",'universe_boundary')
        universe.extend(page);after=last
    if not universe:raise ValueError('Published tradable universe is empty')
    rows=[]
    grouped={}
    for u in universe:grouped.setdefault(u['ticker'],[]).append(u)
    if args.tickers:
        if set(args.tickers)-grouped.keys():raise ValueError('Requested ticker is not in the published tradable universe')
        grouped={k:v for k,v in grouped.items() if k in args.tickers}
    for offset in range(0,len(grouped),500):
        names=sorted(grouped)[offset:offset+500]
        coverage=client.query(f"SELECT ticker,count() days,sum(event_count) events,max(source_date) last_day FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker IN ({','.join(map(P.literal,names))}) AND source_date BETWEEN '{args.start}' AND '{args.end}' GROUP BY ticker",'coverage')
        by_ticker={r['ticker']:r for r in coverage}
        for ticker in names:
            u=grouped[ticker];c=by_ticker.get(ticker,{})
            reason=''
            if len(u)!=1:reason='ambiguous published ticker identity'
            elif not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}',ticker):reason='unsupported canonical ticker syntax'
            elif u[0].get('massive_ticker') not in (None,'',ticker):reason='published market ticker differs; identity review required'
            elif not c:reason='no certified canonical history in requested range'
            report=root.parent/(root.name+'-v6')/ticker.lower()/'report.json'
            rows.append(dict(ticker=ticker,status='deferred' if reason else 'queued',reason=reason,
                days=int(c.get('days',0)),events=int(c.get('events',0)),
                report=str(report),progress_file=str(root/'workers'/ticker/'progress.json')))
    # Long jobs first reduces the final tail while keeping each ticker ordered.
    rows.sort(key=lambda r:(-r['events'],r['ticker']))
    m=dict(schema_version=2,book_version='causal-swing-closing-book-6',created_at=now(),code_hash=code_hash(),universe_date=stamp,
        universe_hash=P.digest(universe),universe=universe,start=args.start,end=args.end,
        workers=args.workers,threads=args.threads,rows=rows)
    save(root,m);P.save(root/'planning-profiles.json',client.profiles)
    print(f'Frozen universe {stamp}: {len(rows)} tickers',flush=True)
    show(m)


def worker(args):
    from research.mlops.env import load_env_files
    load_env_files([args.env_file],verbose=False)
    import build_swing_structure_book as builder
    m=json.loads((args.runtime/'manifest.json').read_text())
    if m.get('schema_version')!=2 or m.get('book_version')!='causal-swing-closing-book-6':raise ValueError('Not a V6 campaign plan')
    if m['code_hash']!=code_hash():raise ValueError('Worker code differs from frozen plan')
    row=next(r for r in m['rows'] if r['ticker']==args.ticker)
    progress=Path(row['progress_file']);progress.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    def publish(stage,**values):P.save(progress,dict(ticker=args.ticker,stage=stage,updated_at=now(),**values))
    options=SimpleNamespace(start=m['start'],end=m['end'],threads=m['threads'],env_file=args.env_file,stop_file=args.runtime/'STOP')
    try:
        options.survivor_only=True
        options.runtime=args.runtime.parent/(args.runtime.name+'-v6')
        publish('v6')
        builder.run(args.ticker,options)
        report=json.loads(Path(row['report']).read_text())
        proof=json.loads(Path(row['report']).with_name('validation.json').read_text())
        if proof['status']!='passed' or proof['database']!=report['database']:raise ValueError('V6 validation missing')
        publish('completed',database=report['database'],elapsed_seconds=time.perf_counter()-started,report=row['report'])
        return 0
    except KeyboardInterrupt:
        publish('interrupted',elapsed_seconds=time.perf_counter()-started);return 130
    except Exception as exc:
        publish('failed',error=str(exc),elapsed_seconds=time.perf_counter()-started)
        raise


def run(args):
    root=args.runtime;m=json.loads((root/'manifest.json').read_text())
    validate_concurrency(m['workers'],m['threads'])
    if m.get('schema_version')!=2 or m.get('book_version')!='causal-swing-closing-book-6':raise ValueError('Not a V6 campaign plan')
    if m['code_hash']!=code_hash():raise ValueError('Code changed since plan; create a new campaign directory')
    if P.digest(m['universe'])!=m['universe_hash']:raise ValueError('Frozen universe hash mismatch')
    # A crashed controller may leave a worker finishing its session. Never
    # reset its progress or start a duplicate writer while it still owns a lock.
    for row in m['rows']:
        lock=Path(row['progress_file']).parent/'worker.lock'
        if lock.exists():
            with exclusive(lock):pass
    (root/'STOP').unlink(missing_ok=True)
    for row in m['rows']:
        if row['status'] in ('active','interrupted') or args.retry_failed and row['status']=='failed':row['status']='queued'
    active={};last=0.;stopping=False
    try:
        while active or any(r['status']=='queued' for r in m['rows']):
            stopping=stopping or (root/'STOP').exists()
            while not stopping and len(active)<m['workers']:
                row=next((r for r in m['rows'] if r['status']=='queued'),None)
                if row is None:break
                folder=Path(row['progress_file']).parent;folder.mkdir(parents=True,exist_ok=True)
                Path(row['progress_file']).unlink(missing_ok=True)
                log=(folder/'worker.log').open('a',encoding='utf-8')
                command=[sys.executable,'-B',str(Path(__file__).resolve()),'worker','--runtime',str(root),'--ticker',row['ticker'],'--env-file',str(args.env_file)]
                process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                row.update(status='active',started_epoch=time.time(),pid=process.pid)
                active[process.pid]=(process,row,log);save(root,m)
            for pid,(process,row,log) in list(active.items()):
                result=process.poll()
                if result is None:continue
                log.close();p=Path(row['progress_file'])
                progress=json.loads(p.read_text()) if p.exists() else {}
                status='completed' if result==0 and progress.get('stage')=='completed' else 'interrupted' if result==130 else 'failed'
                row.update(status=status,elapsed_seconds=time.time()-row['started_epoch'],exit_code=result,
                    reason=progress.get('error','' if status=='completed' else 'See worker.log'),database=progress.get('database'))
                active.pop(pid);save(root,m)
                print(f'{row["ticker"]}: {status} | {duration(row["elapsed_seconds"])} | {row["reason"]}',flush=True)
            if time.monotonic()-last>=args.progress_seconds:
                # State transitions already persist the manifest. A display
                # heartbeat must not rewrite the frozen universe every second.
                show(m);last=time.monotonic()
            if stopping and not active:break
            try:time.sleep(.5)
            except KeyboardInterrupt:
                (root/'STOP').touch();stopping=True
                print('Stopping at session boundaries; waiting for workers to checkpoint.',flush=True)
    finally:
        if active:
            (root/'STOP').touch()
            for process,row,log in active.values():process.wait();log.close();row['status']='interrupted'
        save(root,m)
    show(m)
    return 1 if any(r['status']=='failed' for r in m['rows']) else 130 if stopping else 2 if any(r['status']=='deferred' for r in m['rows']) else 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan','run','status','stop','worker'))
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--start',default='2025-01-01');p.add_argument('--end',default=date.today().isoformat())
    p.add_argument('--workers',type=int,default=4);p.add_argument('--threads',type=int,default=2)
    p.add_argument('--progress-seconds',type=int,default=1);p.add_argument('--retry-failed',action='store_true')
    p.add_argument('--ticker')
    p.add_argument('--tickers',nargs='+',help='Explicit subset for a pilot; omitted means every published tradable ticker')
    p.add_argument('--env-file',type=Path,default=Path(r'D:\TradingML\secrets\.env'))
    return p


def main():
    p=parser()
    args=p.parse_args();args.runtime=args.runtime.resolve()
    if not RUNTIME.is_dir() or not args.runtime.is_relative_to(RUNTIME):p.error('Use the required D:/TradingML/runtimes root')
    try:validate_concurrency(args.workers,args.threads)
    except ValueError as exc:p.error(str(exc))
    if args.progress_seconds<1 or date.fromisoformat(args.start)>date.fromisoformat(args.end):p.error('Invalid dates or progress interval')
    args.runtime.mkdir(parents=True,exist_ok=True)
    if args.action=='status':show(json.loads((args.runtime/'manifest.json').read_text()));return 0
    if args.action=='stop':(args.runtime/'STOP').touch();print('Stop requested; workers finish their current session.');return 0
    if args.action=='worker':
        if not args.ticker or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}',args.ticker):p.error('Invalid worker ticker')
        folder=args.runtime/'workers'/args.ticker;folder.mkdir(parents=True,exist_ok=True)
        with exclusive(folder/'worker.lock'):return worker(args)
    # OS lock survives neither a crash nor a reboot; no stale PID guessing.
    with exclusive(args.runtime/'controller.lock'):
        if args.action=='plan':plan(args);return 0
        return run(args)


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:print(f'Campaign failed: {exc}',file=sys.stderr);raise SystemExit(1)
