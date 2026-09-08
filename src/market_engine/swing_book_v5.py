"""Causal v5 projection over the shared v4 detector; no market I/O on update."""
from copy import deepcopy
from .swing_book import SwingBook, INTRADAY_VERSION, project
from .resistance_selection import select_areas

VERSION = 'causal-swing-closing-book-5'
CONTRACT = 'resistance-evidence-selection-1'


def projection(engine, now, minimum_score=30., maximum_width_bps=100.):
    supports = [dict(project(r, VERSION), p_norm=None, selection_score=None, load_contract=CONTRACT)
                for r in engine.active.values() if r['state']=='active' and r['scale']=='major' and r['side']=='support']
    by_id = {str(r['level_id']): r for r in engine.active.values()}
    result = supports
    for area in select_areas(engine.active.values(), now, minimum_score=minimum_score, maximum_width_bps=maximum_width_bps):
        if not area['selected']:
            continue
        members = [by_id[k] for k in area['members']]
        result.append(dict(unified_level_id='r:'+area['id'], price=area['price'], lower=area['lower'], upper=area['upper'],
            side=-1, prominence=area['score'], selection_score=area['score'], p_norm=None,
            created_at_ms=int(max(r['pivot_at'] for r in members)*1000),
            confirmed_at_ms=int(max(r['confirmed_at'] for r in members)*1000), lifecycle='active',
            book_version=VERSION, load_contract=CONTRACT, scale='major', timeframes=['1s'],
            member_count=len(members), selection_members=area['members'], sources=[], selection_reasons=area['reasons']))
    return dict(unified_levels=result, load_contract=CONTRACT)


class StreamingSwingBookV5(SwingBook):
    """Completed canonical seconds only. Seed is a compact v4 candidate checkpoint."""
    def __init__(self, seed=None, opening=None, split_factor=1., *, minimum_score=30., maximum_width_bps=100.):
        self.selection_dirty = True
        self.minimum_score, self.maximum_width_bps = minimum_score, maximum_width_bps
        self._selection = None
        self._qualified_references = {}
        super().__init__(seed, opening, split_factor, version=INTRADAY_VERSION)
        if opening is not None:
            self.last_time = opening

    def _level_updated(self, level):
        self.selection_dirty = True
        super()._level_updated(level)
        if hasattr(self, 'revision'):
            self.revision += 1

    def _level_removed(self, key):
        self.selection_dirty = True
        super()._level_removed(key)

    def _publish(self, level, t, reason):
        self.selection_dirty = True
        super()._publish(level,t,reason)

    def _refresh_selection(self):
        if self.selection_dirty or self._selection is None:
            self._selection = projection(self, self.last_time, self.minimum_score, self.maximum_width_bps)
            current = self._selection['unified_levels']
            active_members = {k for row in current for k in row.get('selection_members', [])}
            by_id = {str(r['level_id']): r for r in self.active.values()}
            for key, row in self._qualified_references.items():
                members = [by_id.get(k) for k in row['selection_members']]
                if (members and all(r and r['side']=='resistance' and
                        r['state'] in ('active','awaiting_retest','retest_contact') for r in members)
                        and not active_members.intersection(row['selection_members'])
                        and any(r['state']!='active' for r in members)):
                    lifecycle = ('retest_contact' if any(r['state']=='retest_contact' for r in members)
                                 else 'awaiting_retest')
                    current.append(dict(row, lifecycle=lifecycle, retained_qualified_resistance=True))
            self._qualified_references = {r['unified_level_id']:dict(r) for r in current if r['side']==-1}
            self.selection_dirty = False

    def snapshot(self):
        self._refresh_selection()
        return deepcopy(self._selection)

    def observe(self, *bar):
        # Capture qualification at its causal second, independent of UI polling.
        self._refresh_selection()
        super().observe(*bar)
        self._refresh_selection()
