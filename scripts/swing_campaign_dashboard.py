"""Bounded, stable Rich progress display; never writes campaign state."""
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text


def duration(seconds):
    if seconds is None:
        return '--'
    seconds = max(0, int(seconds))
    return f'{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}'


class Dashboard:
    def __init__(self, manifest, console=None):
        self.console = console or Console()
        self.slots = [None] * manifest['workers']
        self.cache = {}
        self.page = 0
        self.pages = 1
        self.message = 'Ctrl+C stops safely after current sessions checkpoint.'
        self.live = None
        self.last_plain = None

    def start(self):
        if self.console.is_terminal:
            self.live = Live(console=self.console, screen=True, auto_refresh=False,
                             redirect_stdout=False, redirect_stderr=False)
            self.live.start()

    def stop(self, manifest):
        if self.live:
            self.live.stop()
        self.console.print(self.overall(manifest, False, finished=True))
        self.console.print(Text(self.message))

    def event(self, message):
        self.message = message

    def report(self, row):
        path = Path(row['report'])
        previous = self.cache.get(str(path))
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if previous and previous[0] == stamp:
                return previous[1], False
            data = json.loads(path.read_text())
            profiles = data.get('session_profiles', [])
            result = (len(profiles), profiles[-1]['session'] if profiles else '--')
            self.cache[str(path)] = (stamp, result)
            return result, False
        except (OSError, ValueError, KeyError):
            return previous[1] if previous else (0, '--'), bool(previous)

    def overall(self, manifest, stopping, finished=False):
        counts = Counter(row['status'] for row in manifest['rows'])
        total = len(manifest['rows'])
        done = counts['completed']
        completed = [r['elapsed_seconds'] for r in manifest['rows']
                     if r['status'] == 'completed' and r.get('elapsed_seconds') is not None]
        remaining = counts['active'] + counts['queued']
        eta = sum(completed) / len(completed) * remaining / manifest['workers'] if completed else None
        state = 'STOPPING — waiting for checkpoints' if stopping else 'RUNNING' if counts['active'] or counts['queued'] else 'FINISHED'
        if finished:
            state = 'STOPPED — resume available' if counts['queued'] or counts['interrupted'] else 'FAILED' if counts['failed'] else 'FINISHED'
        text = Text(f'{state}   Completed {done:,}/{total:,} tickers ({done/max(1,total):.0%})   Estimated ETA {duration(eta)}\n')
        text.append('  '.join(f'{label} {counts[label]:,}' for label in
                             ('active', 'queued', 'completed', 'deferred', 'failed', 'interrupted')))
        return Panel(Group(text, ProgressBar(total=max(1,total), completed=done)),
                     title='V6 swing book • campaign progress', border_style='cyan')

    def bind(self, manifest):
        assigned = {id(row) for row in self.slots if row and row['status'] == 'active'}
        for row in manifest['rows']:
            if row['status'] == 'active' and id(row) not in assigned:
                for index, old in enumerate(self.slots):
                    if old is None or old['status'] != 'active':
                        self.slots[index] = row
                        assigned.add(id(row))
                        break

    def render(self, manifest, stopping=False):
        self.bind(manifest)
        width, height = self.console.size
        columns = max(1, min(2, width // 65))
        capacity = max(1, height - 12)
        per_page = capacity * columns
        self.pages = max(1, math.ceil(len(self.slots) / per_page))
        self.page = min(self.page, self.pages-1)
        first = self.page * per_page
        grid = Table.grid(expand=True, padding=(0, 1))
        for _ in range(columns):
            grid.add_column(ratio=1)
        tables = []
        for column in range(columns):
            table = Table(expand=True, box=None, padding=(0, 1))
            for label in ('ID', 'Ticker', 'State / sessions', 'Progress', 'Elapsed'):
                table.add_column(label, no_wrap=True, overflow='ellipsis')
            for index in range(first+column*capacity, min(first+(column+1)*capacity,len(self.slots))):
                row = self.slots[index]
                if row is None:
                    table.add_row(f'{index+1:02}', '—', 'idle', '', '--')
                    continue
                (done, session), stale = self.report(row)
                total = row['days']
                state = row['status']
                elapsed = time.time()-row.get('started_epoch',time.time()) if state=='active' else row.get('elapsed_seconds')
                label = f'{state} {done}/{total}' + (' stale' if stale else '')
                progress = Table.grid(padding=(0,1))
                progress.add_row(ProgressBar(total=max(1,total),completed=min(done,total),width=8),
                                 Text(f'{min(done,total)/max(1,total):.0%}'))
                table.add_row(f'{index+1:02}', Text(row['ticker']), Text(label),
                              progress, duration(elapsed))
            tables.append(table)
        grid.add_row(*tables)
        footer = Text(f'Workers {first+1}–{min(first+per_page,len(self.slots))}/{len(self.slots)} | Page {self.page+1}/{self.pages} | N/P: page | Ctrl+C: stop\n', no_wrap=True, overflow='ellipsis')
        footer.append(self.message)
        return Group(self.overall(manifest, stopping), Panel(grid, title='Workers • completed sessions', padding=(0,0)), footer)

    def keys(self):
        if self.live and os.name == 'nt':
            import msvcrt
            while msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if key == '\x03':raise KeyboardInterrupt
                if key == 'n': self.page = min(self.pages-1,self.page+1)
                elif key == 'p': self.page = max(0,self.page-1)

    def update(self, manifest, stopping=False):
        if self.live:
            self.keys()
            self.live.update(self.render(manifest,stopping), refresh=True)
        else:
            counts = Counter(row['status'] for row in manifest['rows'])
            snapshot = (tuple(sorted(counts.items())),stopping,self.message)
            if snapshot != self.last_plain:
                self.console.print(Text(' | '.join(f'{key}={value}' for key,value in sorted(counts.items()))))
                self.last_plain = snapshot
