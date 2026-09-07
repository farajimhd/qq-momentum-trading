from copy import deepcopy
from random import Random

import pytest

from src.market_engine.swing_book import SwingBook


class ScannedBook(SwingBook):
    def _levels_to_update(self, *args):
        return list(self.active)

    def _level_updated(self, level):
        pass

    def _level_removed(self, key):
        pass


@pytest.mark.parametrize('rng_seed', [3, 19, 71])
def test_index_matches_scan_at_every_bar_and_across_session_split(rng_seed):
    rng = Random(rng_seed)
    indexed, scanned = SwingBook(), ScannedBook()
    for engine in (indexed, scanned):
        for j in range(80):
            engine._found({'scale': 'major' if j % 3 else 'local'},
                          (.5 + j*.13, 1., .05), 'support' if j % 2 else 'resistance', 2.)
    price, stamp = 5., 10.
    for i in range(1500):
        if i in (500, 1000):
            states = [e.closing_state(stamp) for e in (indexed, scanned)]
            assert states[0] == states[1]
            stamp += 86400
            indexed = SwingBook(states[0], stamp, .5)
            scanned = ScannedBook(states[1], stamp, .5)
            price *= .5
        stamp += 1900 if i % 149 == 0 else 1
        price = max(.03, price + rng.uniform(-.15, .15))
        if i % 97 == 0:
            price = rng.uniform(.1, 12.)
        high, low = price + rng.random()*.09, max(.001, price-rng.random()*.09)
        indexed.observe(stamp, high, low, price)
        scanned.observe(stamp, high, low, price)
        assert indexed.active == scanned.active, (rng_seed, i)
        assert indexed.counts == scanned.counts
        assert indexed.revision == scanned.revision


def test_remote_dormant_levels_are_not_scanned_and_gap_restores_them():
    engine = SwingBook()
    for j in range(100):
        engine._found({'scale': 'major'}, (20.+j, 1., .3), 'support', 2.)
    engine.observe(3., 5., 5., 5.)
    engine.observe(4., 5., 5., 5.)
    assert all(l['state']=='awaiting_retest' for l in engine.active.values())
    assert engine._levels_to_update(5., 5., 5., 5., .01) == []
    before = deepcopy(engine.active)
    engine.observe(5., 5., 5., 5.)
    assert engine.active == before
    engine.observe(6., 150., 150., 150.)
    assert all(l['state']=='active' for l in engine.active.values())


def test_historical_capacity_is_separate_from_session_preview_budget():
    from src.market_engine.swing_structure import SwingStructure
    assert SwingStructure().settings.max_active == 2048
    book = SwingBook()
    for j in range(2050):
        book._found({'scale':'major'}, (20.+j,1.,.3),'support',2.)
    assert len(book.active) == 2050
    assert book.settings.max_active == 8192
