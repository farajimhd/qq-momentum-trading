"""Compact closing state and causal continuation of the accepted swing detector.

Only major levels are published. Local levels remain compact detector state;
neither bars nor the detector's visual segment history belong in a checkpoint.
Expiry counts session time, with the overnight gap paused.
"""
from copy import deepcopy
from math import isfinite, log1p
from statistics import median

from .swing_structure import SwingStructure, SwingSettings
from .swing_level_index import SwingLevelIndex

LEGACY_VERSION = 'causal-swing-closing-book-1'
VERSION = 'causal-swing-closing-book-2'
QUALIFIED_VERSION = 'causal-swing-closing-book-3'
VERSIONS = (LEGACY_VERSION, VERSION, QUALIFIED_VERSION)
PRICE_STATE_FIELDS = ('price', 'lower', 'upper', 'reversal_distance',
                      'history_threshold', 'retest_threshold', 'best_departure')


class SwingBook(SwingStructure):
    def __init__(self, seed=None, opening=None, split_factor=1., *, version=VERSION):
        # Historical carry contains dormant anchors from many sessions. This
        # is a fail-closed memory budget, not a level selection/truncation rule.
        super().__init__(SwingSettings(max_active=8192 if version != LEGACY_VERSION else 2048))
        if version not in VERSIONS:
            raise ValueError('Unsupported swing book version')
        self.version = version
        self.level_index = SwingLevelIndex() if version != LEGACY_VERSION else None
        self.history_watch = set()
        self.retired = set()
        self.session_high = self.session_low = None
        self.history_bar = None
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
            for key in PRICE_STATE_FIELDS:
                if level.get(key) is not None:
                    level[key] *= split_factor
            # An overnight gap is not a touch or a consecutive breakout bar.
            level.update(beyond=0, touching=False, previous_contact=False)
            if version == QUALIFIED_VERSION and level['scale'] == 'major':
                level['retest_pending_at'] = None
            self.active[level['level_id']] = level
            self._level_updated(level)
            self.persisted_ids.add(level['level_id'])

    def _levels_to_update(self, t, high, low, close, tick):
        self.history_bar = (t, high, low, close)
        if self.level_index is None:
            return super()._levels_to_update(t, high, low, close, tick)
        return sorted(set(self.level_index.candidates(t, high, low, close, tick)) | self.history_watch)

    def _level_updated(self, level):
        if self.version == QUALIFIED_VERSION and level['scale'] == 'major':
            self._update_history_evidence(level)
        if self.level_index is not None:
            self.level_index.update(level, self.settings.local_lifetime_seconds)

    def _level_removed(self, key):
        self.history_watch.discard(key)
        if self.level_index is not None:
            self.level_index.remove(key)

    def _expired(self, level, t):
        if self.version!=LEGACY_VERSION and level['scale']=='major':
            return False
        return super()._expired(level,t)

    def _publish(self, level, t, reason):
        if self.version == QUALIFIED_VERSION and level['scale'] == 'major' and reason == 'accepted_break':
            level['accepted_crossings'] += 1
            level['retest_pending_at'] = None
            level['history_away'] = False
            if level['accepted_crossings'] >= 2:
                self.retired.add(level['level_id'])
        self.revision += 1
        self.counts['events_'+reason] += 1

    def _update_history_evidence(self, level):
        if 'history_threshold' not in level:
            noise = median(self.ranges) if self.ranges else 0.
            span = (self.session_high-self.session_low) if self.session_high is not None else 0.
            tick = .0001 if level['price'] < 1 else .01
            level.update(history_threshold=max(.03*level['price'], 6*noise, .15*span, 3*tick),
                         retest_threshold=max(.01*level['price'], 3*noise, .05*span, 2*tick),
                         best_departure=0., independent_retests=0, accepted_crossings=0,
                         history_away=False, retest_pending_at=None, history_observed_at=None)
        bar = self.history_bar
        key = level['level_id']
        if bar and level['history_observed_at'] != bar[0] and level['state'] == 'active' and key not in self.retired:
            t, high, low, close = bar
            contact = low <= level['upper'] and high >= level['lower']
            if (key in self.persisted_ids and level['history_away'] and
                    level['retest_pending_at'] is None and not contact):
                # An already-qualified, departed anchor needs no evidence
                # update until another contact. Keep scan/index state exact.
                self.history_watch.discard(key)
                return
            level['history_observed_at'] = t
            departure = max(0., close-level['price'] if level['side']=='support' else level['price']-close)
            level['best_departure'] = max(level['best_departure'], departure)
            if contact and level['history_away']:
                level['retest_pending_at'] = t
                level['history_away'] = False
            if departure >= level['retest_threshold'] and not contact:
                pending = level['retest_pending_at']
                if pending is not None and t > pending:
                    level['independent_retests'] += 1
                    level['accepted_crossings'] = 0
                    level['retest_pending_at'] = None
                # A touch and departure inside one OHLC bar are not an
                # independent retest: wait for the next completed bar.
                if level['retest_pending_at'] is None:
                    level['history_away'] = True
        if (level['state']=='active' and key not in self.retired and
                (key not in self.persisted_ids or level['retest_pending_at'] is not None or not level['history_away'])):
            self.history_watch.add(key)
        else:
            self.history_watch.discard(key)

    def observe(self, t, high, low, close):
        super().observe(t, high, low, close)
        if self.version == QUALIFIED_VERSION:
            self.session_high = max(high, self.session_high) if self.session_high is not None else high
            self.session_low = min(low, self.session_low) if self.session_low is not None else low
            for key in sorted(self.retired):
                if key in self.active:
                    del self.active[key]
                    self._level_removed(key)
                    self.revision += 1
                    self.counts['events_history_retired'] += 1
            self.retired.clear()

    def _qualified_for_carry(self, level):
        if self.version != QUALIFIED_VERSION or level['scale'] != 'major' or level['level_id'] in self.persisted_ids:
            return True
        span = self.session_high-self.session_low if self.session_high is not None else 0.
        return (level['best_departure'] >= max(level['history_threshold'], .15*span) or
                (level['independent_retests'] >= 2 and level['best_departure'] >= max(level['retest_threshold'], .05*span)))

    def closing_state(self, closed_at):
        if closed_at < self.last_time:
            raise ValueError('Closing timestamp precedes observed bars')
        levels = []
        for level in self.active.values():
            # Preserve dormant historical anchors for later retest, but never
            # persist a transient level that broke before its first close.
            historical = (self.version!=LEGACY_VERSION and level['scale']=='major'
                          and level['level_id'] in self.persisted_ids)
            if not self._expired(level,closed_at) and (level['state']=='active' or historical) and self._qualified_for_carry(level):
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
