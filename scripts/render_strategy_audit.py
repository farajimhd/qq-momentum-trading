"""Render canonical trade prices, actual positions and effective protection paths."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json


def price_line_with_gaps(timestamps, prices, maximum_gap_seconds):
    """Keep every observed price; insert separators across unobserved time."""
    import numpy as np
    times, values = np.asarray(timestamps, dtype=float), np.asarray(prices, dtype=float)
    if (not np.isfinite(maximum_gap_seconds) or maximum_gap_seconds <= 0
            or times.ndim != 1 or values.shape != times.shape
            or not np.all(np.isfinite(times)) or not np.all(np.isfinite(values))
            or np.any(values <= 0) or np.any(np.diff(times) < 0)):
        raise ValueError('Require ordered finite trade times, positive prices and a positive line-gap limit')
    gaps = np.flatnonzero(np.diff(times) > maximum_gap_seconds) + 1
    return np.insert(times, gaps, times[gaps]), np.insert(values, gaps, np.nan), len(gaps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--maximum-line-gap-seconds', type=float, default=5.,
        help='Break the price path after this long without an eligible trade; preserves all observations')
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    import numpy as np
    b = json.loads(args.benchmark.read_text())
    r = json.loads(args.results.read_text())
    ny = ZoneInfo('America/New_York')
    def dt(value):
        return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ny)
    line_times, line_prices, gap_count = price_line_with_gaps(
        b['eligible_trade_times'], b['eligible_trade_prices'], args.maximum_line_gap_seconds)
    times = [datetime.fromtimestamp(t,ny) for t in line_times]
    # A lone trade between two gaps needs a marker: a line has no segment there.
    isolated = (np.flatnonzero(np.isfinite(line_prices)
        & np.r_[True, np.isnan(line_prices[:-1])]
        & np.r_[np.isnan(line_prices[1:]), True]).tolist() if len(line_prices) else [])
    positions = sorted(r['position_lifecycles'],key=lambda p:p['opened_at'])
    figure, axes = plt.subplots(2,1,figsize=(17,10),constrained_layout=True)
    biggest = max((p for p in b['benchmark']['positions'] if p['direction']=='long'),
                  key=lambda p:p['gross_return_bps'],default=None)
    for ax in axes:
        ax.plot(times,line_prices,color='#37474f',linewidth=.65,
            marker='.', markersize=2, markevery=isolated,
            label=f'Eligible trade price (breaks after >{args.maximum_line_gap_seconds:g}s without trades)')
        for side, marker, color in [('BUY', '^', '#1565c0'), ('SELL', 'v', '#8e24aa')]:
            fills = [e for e in r['executions'] if e['side'] == side]
            if fills:
                ax.scatter([dt(e['source_event_time']) for e in fills],
                    [float(e['price']) for e in fills], marker=marker, color=color,
                    s=12, alpha=.7, zorder=4, label=f'{side.title()} fills')
        for n,p in enumerate(positions,1):
            start,end = dt(p['opened_at']),dt(p['closed_at'] or r['run']['session_end'])
            entry,exit_price = float(p['entry_price']),float(p['exit_price'] or p['entry_price'])
            color = '#00897b' if float(p['net_pnl'] or 0)>0 else '#d84315'
            ax.plot([start,end],[entry,exit_price],color=color,linewidth=1.4,marker='o',markersize=3)
            ax.annotate(str(n),(start,entry),xytext=(0,8),textcoords='offset points',fontsize=7,color=color)
            for kind,linecolor in [('stop','#c62828'),('target','#1565c0')]:
                timeline = sorted((e for e in p.get('protection_timeline',[]) if e['kind']==kind and e['phase']=='effective'),
                                  key=lambda e:(e['event_time'],e['sequence']))
                active = None
                for event in timeline:
                    t = dt(event['event_time'])
                    if active:
                        ax.plot([active[0],t],[active[1],active[1]],color=linecolor,alpha=.35,linewidth=.6)
                    active = (t,float(event['price'])) if event.get('active') and event.get('price') else None
                if active:
                    ax.plot([active[0],end],[active[1],active[1]],color=linecolor,alpha=.35,linewidth=.6)
        ax.xaxis.set_major_formatter(dates.DateFormatter('%H:%M:%S',tz=ny))
        ax.set_ylabel('Price ($)')
        ax.grid(alpha=.15)
    axes[0].set_title(f"{b['symbol']} | {r['run'].get('configuration_label','Research candidate')} | Actual fills and effective protection")
    axes[0].legend(loc='upper left', fontsize=8)
    if biggest:
        width = max(10.,biggest['exit_time']-biggest['entry_time'])
        left,right = biggest['entry_time']-.3*width,biggest['exit_time']+.3*width
        axes[1].set_xlim(datetime.fromtimestamp(left,ny),datetime.fromtimestamp(right,ny))
        visible = [p for t,p in zip(b['eligible_trade_times'],b['eligible_trade_prices']) if left<=t<=right]
        if visible:
            margin = max(max(visible)-min(visible),max(visible)*.01)*.1
            axes[1].set_ylim(min(visible)-margin,max(visible)+margin)
        axes[1].set_title('Largest price-only hindsight episode — offline diagnostic, not an executable return')
    axes[1].set_xlabel('New York time | triangles: individual fills; green/red lines: position average prices; faint red/blue: effective stop/target')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    figure.savefig(args.output,dpi=150)
    plt.close(figure)
    print(f'Chart complete | positions {len(positions)} | price-path gaps {gap_count} | {args.output}')


if __name__ == '__main__':
    main()
