"""Compact closing state and causal continuation of the accepted swing detector.

Only major levels are published. Local levels remain compact detector state;
neither bars nor the detector's visual segment history belong in a checkpoint.
Expiry counts session time, with the overnight gap paused.
"""
from copy import deepcopy
from math import isfinite, log1p

from .swing_structure import SwingStructure

VERSION = 'causal-swing-closing-book-1'


class SwingBook(SwingStructure):
    def __init__(self, seed=None, opening=None, split_factor=1.):
        super().__init__()
        self.revision = 0
        if not isfinite(split_factor) or split_factor <= 0:
            raise ValueError('Invalid split factor')
        if seed is None:
            return
        if seed['version'] != VERSION or opening < seed['closed_at']:
            raise ValueError('Incompatible or future closing state')
        self.sequence = seed['sequence']
        pause = opening-seed['closed_at']
        for source in seed['levels']:
            level = deepcopy(source)
            level['last_test'] += pause
            for key in ('price', 'lower', 'upper', 'reversal_distance'):
                if level[key] is not None:
                    level[key] *= split_factor
            # An overnight gap is not a touch or a consecutive breakout bar.
            level.update(beyond=0, touching=False, previous_contact=False)
            self.active[level['level_id']] = level

    def _publish(self, level, t, reason):
        self.revision += 1
        self.counts['events_'+reason] += 1

    def closing_state(self, closed_at):
        if closed_at < self.last_time:
            raise ValueError('Closing timestamp precedes observed bars')
        levels = []
        for level in self.active.values():
            ttl = (self.settings.local_lifetime_seconds if level['scale']=='local'
                   else self.settings.major_lifetime_seconds)
            if closed_at-level['last_test'] < ttl and level['state']=='active':
                levels.append(deepcopy(level))
        return dict(version=VERSION, closed_at=closed_at, sequence=self.sequence,
                    levels=sorted(levels, key=lambda r:r['level_id']))

    def snapshot(self):
        return {'unified_levels': [project(level) for level in self.active.values()
                                  if level['scale']=='major']}


def project(level):
    return dict(unified_level_id=str(level['level_id']), price=level['price'],
        lower=level['lower'], upper=level['upper'],
        side=1 if level['side']=='support' else -1,
        prominence=log1p(level['strength']),
        created_at_ms=int(level['pivot_at']*1000),
        confirmed_at_ms=int(level['confirmed_at']*1000),
        lifecycle=level['state'], pending_side=(0 if level['state']=='active' else -1 if level['side']=='support' else 1),
        book_version=VERSION,sources=[],ticker_relative_quality_status='unavailable',
        timeframes=['1s'], scale=level['scale'],
        confirmation_kind=level['confirmation_kind'])
