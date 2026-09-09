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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    b = json.loads(args.benchmark.read_text())
    r = json.loads(args.results.read_text())
    ny = ZoneInfo('America/New_York')
    def dt(value):
        return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ny)
    times = [datetime.fromtimestamp(t,ny) for t in b['eligible_trade_times']]
    positions = sorted(r['position_lifecycles'],key=lambda p:p['opened_at'])
    figure, axes = plt.subplots(2,1,figsize=(17,10),constrained_layout=True)
    biggest = max((p for p in b['benchmark']['positions'] if p['direction']=='long'),
                  key=lambda p:p['gross_return_bps'],default=None)
    for ax in axes:
        ax.plot(times,b['eligible_trade_prices'],color='#37474f',linewidth=.65,label='Eligible trade price')
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
    if biggest:
        width = max(10.,biggest['exit_time']-biggest['entry_time'])
        left,right = biggest['entry_time']-.3*width,biggest['exit_time']+.3*width
        axes[1].set_xlim(datetime.fromtimestamp(left,ny),datetime.fromtimestamp(right,ny))
        visible = [p for t,p in zip(b['eligible_trade_times'],b['eligible_trade_prices']) if left<=t<=right]
        if visible:
            margin = max(max(visible)-min(visible),max(visible)*.01)*.1
            axes[1].set_ylim(min(visible)-margin,max(visible)+margin)
        axes[1].set_title('Largest price-only hindsight episode — offline diagnostic, not an executable return')
    axes[1].set_xlabel('New York time | green/red: profitable/losing position; faint red/blue: effective stop/target')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    figure.savefig(args.output,dpi=150)
    plt.close(figure)
    print(f'Chart complete | positions {len(positions)} | {args.output}')


if __name__ == '__main__':
    main()
