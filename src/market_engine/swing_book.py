"""Compact closing state and causal continuation of the accepted swing detector.

Only major levels are published. Local levels remain compact detector state;
neither bars nor the detector's visual segment history belong in a checkpoint.
Expiry counts session time, with the overnight gap paused.
"""
from copy import deepcopy
from math import isfinite, log1p

from .swing_structure import SwingStructure
from .swing_level_index import SwingLevelIndex

LEGACY_VERSION = 'causal-swing-closing-book-1'
VERSION = 'causal-swing-closing-book-2'
VERSIONS = (LEGACY_VERSION, VERSION)


class SwingBook(SwingStructure):
    def __init__(self, seed=None, opening=None, split_factor=1., *, version=VERSION):
        super().__init__()
        if version not in VERSIONS:
            raise ValueError('Unsupported swing book version')
        self.version = version
        self.level_index = SwingLevelIndex() if version == VERSION else None
        self.persisted_ids = set()
        self.revision = 0
        if not isfinite(split_factor) or split_factor <= 0:
            raise ValueError('Invalid split factor')
        if seed is None:
            return
        if seed['version'] != version or opening < seed['closed_at']:
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
            self._level_updated(level)
            self.persisted_ids.add(level['level_id'])

    def _levels_to_update(self, t, high, low, close, tick):
        if self.level_index is None:
            return super()._levels_to_update(t, high, low, close, tick)
        return self.level_index.candidates(t, high, low, close, tick)

    def _level_updated(self, level):
        if self.level_index is not None:
            self.level_index.update(level, self.settings.local_lifetime_seconds)

    def _level_removed(self, key):
        if self.level_index is not None:
            self.level_index.remove(key)

    def _expired(self, level, t):
        if self.version==VERSION and level['scale']=='major':
            return False
        return super()._expired(level,t)

    def _publish(self, level, t, reason):
        self.revision += 1
        self.counts['events_'+reason] += 1

    def closing_state(self, closed_at):
        if closed_at < self.last_time:
            raise ValueError('Closing timestamp precedes observed bars')
        levels = []
        for level in self.active.values():
            # Preserve dormant historical anchors for later retest, but never
            # persist a transient level that broke before its first close.
            historical = (self.version==VERSION and level['scale']=='major'
                          and level['level_id'] in self.persisted_ids)
            if not self._expired(level,closed_at) and (level['state']=='active' or historical):
                levels.append(deepcopy(level))
        return dict(version=self.version, closed_at=closed_at, sequence=self.sequence,
                    levels=sorted(levels, key=lambda r:r['level_id']))

    def snapshot(self):
        return {'unified_levels': [project(level,self.version) for level in self.active.values()
                                  if level['scale']=='major' and
                                  (self.version==LEGACY_VERSION or level['state']=='active')]}


def project(level, version=VERSION):
    return dict(unified_level_id=str(level['level_id']), price=level['price'],
        lower=level['lower'], upper=level['upper'],
        side=1 if level['side']=='support' else -1,
        prominence=log1p(level['strength']),
        created_at_ms=int(level['pivot_at']*1000),
        confirmed_at_ms=int(level['confirmed_at']*1000),
        lifecycle=level['state'], pending_side=(0 if level['state']=='active' else -1 if level['side']=='support' else 1),
        book_version=version,sources=[],ticker_relative_quality_status='unavailable',
        timeframes=['1s'], scale=level['scale'],
        confirmation_kind=level['confirmation_kind'])
