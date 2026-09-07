"""Derived indexes for the swing state machine; never persisted as authority."""
from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
from heapq import heappop, heappush


class SwingLevelIndex:
    def __init__(self):
        self.records = {}
        self.boundaries = defaultdict(list)
        self.reset = set()
        self.deadlines = []
        self.tree = None
        self.dirty = False

    def remove(self, key):
        old = self.records.pop(key, None)
        if old is None:
            return
        lower, upper, side, state, touching, deadline = old
        for name, value in (('lower', lower), ('upper', upper)):
            values = self.boundaries[(side, state, name)]
            values.pop(bisect_left(values, (value, key)))
        if touching and state == 'active':
            values = self.boundaries[(side, 'touching', 'upper' if side == 'support' else 'lower')]
            value = upper if side == 'support' else lower
            values.pop(bisect_left(values, (value, key)))
        self.reset.discard(key)
        self.dirty = True

    def update(self, level, local_lifetime):
        key = level['level_id']
        deadline = level['last_test'] + local_lifetime if level['scale'] == 'local' else None
        record = (level['lower'], level['upper'], level['side'], level['state'], level['touching'], deadline)
        old = self.records.get(key)
        if old != record:
            geometry_changed = old is None or old[:2] != record[:2]
            was_dirty = self.dirty
            self.remove(key)
            self.records[key] = record
            lower, upper, side, state, touching, _ = record
            for name, value in (('lower', lower), ('upper', upper)):
                insort(self.boundaries[(side, state, name)], (value, key))
            if touching and state == 'active':
                name, value = ('upper', upper) if side == 'support' else ('lower', lower)
                insort(self.boundaries[(side, 'touching', name)], (value, key))
            self.dirty = was_dirty or geometry_changed
            if deadline is not None:
                heappush(self.deadlines, (deadline, key))
        if level['previous_contact'] or (level['state'] == 'active' and level['beyond']):
            self.reset.add(key)
        else:
            self.reset.discard(key)
        # Stale expiry entries must not grow with the number of touches.
        if len(self.deadlines) > 4 * len(self.records) + 64:
            from heapq import heapify
            self.deadlines = [(r[-1], k) for k, r in self.records.items() if r[-1] is not None]
            heapify(self.deadlines)

    def _contacts(self, low, high):
        if self.dirty:
            values = sorted((r[0], r[1], k) for k, r in self.records.items())
            def build(a, b):
                if a >= b:
                    return None
                m = (a + b) // 2
                left, right = build(a, m), build(m + 1, b)
                lower, upper, key = values[m]
                maximum = max(upper, left[0] if left else upper, right[0] if right else upper)
                return maximum, lower, upper, key, left, right
            self.tree = build(0, len(values))
            self.dirty = False
        found = set()
        def visit(node):
            if node is None or node[0] < low:
                return
            _, lower, upper, key, left, right = node
            visit(left)
            if lower <= high:
                if upper >= low:
                    found.add(key)
                visit(right)
        visit(self.tree)
        return found

    def candidates(self, t, high, low, close, tick):
        found = self._contacts(low, high) | self.reset
        def below(side, state, name, price):
            values = self.boundaries[(side, state, name)]
            # Widen only candidate selection to tolerate floating-point
            # reassociation. The unchanged state machine makes the decision.
            found.update(k for _, k in values[:bisect_right(values, (price + abs(price)*1e-14, float('inf')))])
        def above(side, state, name, price):
            values = self.boundaries[(side, state, name)]
            found.update(k for _, k in values[bisect_left(values, (price - abs(price)*1e-14, -1)):])
        # Two-close acceptance and subsequent retest departure can occur on gaps.
        for state in ('active', 'retest_contact'):
            above('support', state, 'lower', close + tick)
            below('resistance', state, 'upper', close - tick)
        # A failed break restores the old role; an active touched level rejects.
        for state in ('awaiting_retest', 'retest_contact', 'touching'):
            below('support', state, 'upper', close - tick)
            above('resistance', state, 'lower', close + tick)
        while self.deadlines and self.deadlines[0][0] <= t:
            deadline, key = heappop(self.deadlines)
            if key in self.records and self.records[key][-1] == deadline:
                found.add(key)
        return sorted(found)
