"""Discovery replay of immutable features; not the Portfolio/OMS backtest.

Fill proxies use the next fresh quoted second, ask buys/bid sells, 5 bps
additional slippage per side. Labels are joined only after trading ends.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import argparse
import json
import subprocess
from hashlib import sha256
from datetime import datetime,timezone
from collections import Counter,deque
from time import perf_counter
import polars as pl
from src.trading_runtime import swing_momentum as M
from src.backend.swing_book_cursor import SwingBookCursor
from src.market_engine.historical_source import QmdHistoricalEventSource,_source_revision
from src.backend.qmd_gateway_client import qmd_history_base_url
from zoneinfo import ZoneInfo


def replay(rows,contexts,settings):
    position=None; pending=None; red=0.; state={}; trades=[]; reasons=Counter();history=deque()
    for r,ctx in zip(rows,contexts):
        price=r['close'];t=r['t'];tick=.01 if price>=1 else .0001
        bid=r.get('bid') or 0;ask=r.get('ask') or 0
        fresh=bool(r.get('quote_ready') and 0<bid<=ask and (r.get('quote_age_ms') or 0)<=1000)
        while len(history)>1 and history[1][0]<=t-3:history.popleft()
        progress=price-history[0][1] if history and t-4<=history[0][0]<=t-3 else None
        history.append((t,price))
        if pending and pending[0]=='buy' and t>pending[1]['at']+1:
            reasons['entry_expired_before_fill']+=1;pending=None
        if pending and fresh:
            action,decision=pending
            if action=='buy':
                if ask>decision['stop'] and (ask-bid)/((ask+bid)/2)*10000<=200:
                    position=dict(entry=t,buy=ask*1.0005,stop=decision['stop'])
                    state={}
                else:reasons['fill_rejected']+=1
            elif position:
                trades.append(dict(position,exit=t,sell=bid*.9995,reason=decision,
                                   pnl=bid*.9995-position['buy']))
                position=None;state={}
            pending=None
        if pending:
            continue
        if price<r['open']:red=price
        bullish=r['macd_line'] is not None and r['macd_signal'] is not None and r['macd_line']>r['macd_signal']
        if position:
            reason=''
            if settings is not None:
                position['stop'],reason=M.manage(ctx,price,t,position['entry'],position['stop'],tick,state,settings)
            if price<=position['stop']:reason='protective_stop'
            elif not bullish:reason='completed_macd_closed'
            if reason:pending=('sell',reason)
        elif bullish:
            if not fresh or (ask-bid)/((ask+bid)/2)*10000>200:
                reasons['execution_unavailable']+=1;continue
            if r.get('pressure_usable') and (r.get('fast_trade_imbalance') or 0)<-.3:
                reasons['selling_veto']+=1;continue
            stop=M.initial_stop(ctx,price,red,tick) if settings is not None else red-tick if 0<red<price else 0
            reason=M.entry(ctx,price,r['open'],stop,settings,progress=progress,spread=ask-bid) if settings is not None else '' if 0<stop<price else 'stop_unavailable'
            if reason:reasons[reason]+=1
            else:pending=('buy',dict(stop=stop,at=t))
    return dict(trades=trades,rejections=dict(reasons),unclosed_position=position,pending=pending,
                net_per_share=sum(t['pnl'] for t in trades),count=len(trades),
                wins=sum(t['pnl']>0 for t in trades))


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--features-root',type=Path,required=True)
    a.add_argument('--output',type=Path,required=True)
    a.add_argument('--book',action='append',required=True,help='TICKER=versioned book id')
    args=a.parse_args()
    if not args.output.resolve().is_relative_to(Path('D:/TradingML/runtimes')):
        raise ValueError('Output must be in the operational runtime root')
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'run-manifest.json').write_text(json.dumps(dict(
        family='swing_momentum',version=1,job='discovery_execution_proxy',
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        evaluator_hash=sha256(Path(__file__).read_bytes()).hexdigest(),
        policy_hash=sha256(Path(M.__file__).read_bytes()).hexdigest(),
        features_root=str(args.features_root.resolve()),output=str(args.output.resolve()),
        books=args.book,minimum_p_norm=.2,slippage_bps_per_side=5,
        maximum_spread_bps=200,entry_expiry_seconds=1),indent=2))
    result=[]
    for item in args.book:
        ticker,book=item.split('=',1);started=perf_counter()
        path=args.features_root/f'{ticker}-features.parquet'
        rows=pl.read_parquet(path).sort('t').to_dicts()
        manifest=json.loads((args.features_root/f'{ticker}-manifest.json').read_text())
        assert manifest['source_revision']['complete_for_history'] and manifest['source_revision']['request_complete']
        day=manifest['session'];ny=ZoneInfo('America/New_York')
        source=QmdHistoricalEventSource(qmd_history_base_url(),
            start=datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ny),
            end=datetime.fromisoformat(day+'T20:00:00').replace(tzinfo=ny),
            tickers=[ticker],event_kinds=('trade','quote'))
        current=_source_revision(source._read_page(None,manifest['source_revision'],limit=1))
        assert current['revision_token']==manifest['source_revision']['revision_token']
        assert len(rows)==manifest['bars'] and all(b['t']>a['t'] for a,b in zip(rows,rows[1:]))
        c=SwingBookCursor(book,ticker,normalized=True)
        assert c.build['version']=='causal-swing-closing-book-4'
        cache=args.output/f'{ticker}-context.json'
        identity=[sha256(path.read_bytes()).hexdigest(),c.build['fingerprint'],.2]
        cached=json.loads(cache.read_text()) if cache.exists() else {}
        contexts=cached.get('contexts',[]) if cached.get('identity')==identity else []
        if not contexts:
            for i,r in enumerate(rows):
                snap=c.snapshot(datetime.fromtimestamp(r['t'],timezone.utc))
                contexts.append(M.context(snap['unified_levels'],r['close'],r['t'],.2))
                if i%5000==0:print(f'{ticker}: active=1 frames={i}/{len(rows)} failed=0',flush=True)
            cache.write_text(json.dumps(dict(identity=identity,contexts=contexts)))
        labels=json.loads((args.features_root/f'{ticker}-labels.json').read_text())['positions']
        variants=[('baseline',None),('v4_room1',dict(M.DEFAULTS,minimum_progress_spreads=0))]
        variants += [(f'v4_progress{x}',dict(M.DEFAULTS,minimum_progress_spreads=x)) for x in (1,2)]
        for name,settings in variants:
            run=replay(rows,contexts,settings)
            strong=[l for l in labels if l['direction']=='long' and l['gross_return_bps']>=500]
            captured=sum(any(t['entry']<=l['exit_time'] and t['exit']>=l['macd_open'] for t in run['trades']) for l in strong)
            run.update(ticker=ticker,variant=name,strong_labels=len(strong),strong_intervals_participated=captured,
                       feature_hash=sha256(path.read_bytes()).hexdigest(),book=book,book_fingerprint=c.build['fingerprint'],
                       source_manifest=manifest,settings=settings,elapsed_seconds=perf_counter()-started)
            (args.output/f'{ticker}-{name}.json').write_text(json.dumps(run,indent=2))
            result.append({k:run[k] for k in ('ticker','variant','count','wins','net_per_share','strong_labels','strong_intervals_participated','unclosed_position')})
            print(result[-1],flush=True)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()
