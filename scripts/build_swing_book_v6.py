#!/usr/bin/env python3
"""Build V6 day by day from canonical seconds; persist selected survivors only."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import date
import re
from build_swing_structure_book import run
from swing_book_paths import WORKSTATION_ENV_FILE


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tickers',nargs='+',required=True)
    p.add_argument('--start',default='2025-01-01');p.add_argument('--end',default=date.today().isoformat())
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--env-file',type=Path,default=WORKSTATION_ENV_FILE)
    p.add_argument('--threads',type=int,default=2);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--stop-file',type=Path,help='If this file exists, stop at the next session boundary and retain completed checkpoints')
    a=p.parse_args();a.survivor_only=True
    if len(set(a.tickers))!=len(a.tickers) or any(not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}',t) for t in a.tickers):p.error('Invalid or duplicate ticker')
    if not 1<=a.workers<=8 or not 1<=a.threads<=8 or a.workers*a.threads>16:p.error('Worker/query thread budget must be <=16')
    if date.fromisoformat(a.start)>date.fromisoformat(a.end):p.error('Invalid date range')
    with ProcessPoolExecutor(max_workers=min(a.workers,len(a.tickers))) as pool:
        futures=[pool.submit(run,t,a) for t in a.tickers]
        for future in futures:future.result()


if __name__=='__main__':main()
