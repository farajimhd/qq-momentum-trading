from collections import deque
from copy import deepcopy

from src.market_engine.swing_book import SwingBook, INTRADAY_VERSION


def bracket(bottom, top):
    e = SwingBook(version=INTRADAY_VERSION)
    e._found({'scale':'major'}, (bottom,1.,.1), 'support',2.)
    e._found({'scale':'major'}, (top,1.,.1), 'resistance',2.)
    e.current_close = (bottom+top)/2
    return e


def test_distant_historical_bracket_cannot_suppress_new_high():
    e=bracket(4.25,5.35)
    e.persisted_ids.update(e.active)
    e.approach=deque((t,4.45,4.5,4.4) for t in range(30,60))
    e.current_close=4.4
    assert not e._inside_consolidation(4.5,50.,60.)
    e._found({'scale':'major'},(4.5,50.,.1),'resistance',60.)
    assert any(l['price']==4.5 for l in e.active.values())


def test_compact_bracket_requires_both_edges_visited():
    e=bracket(4.,4.1)
    e.approach=deque((t,4.05,4.06,4.04) for t in range(30,60))
    assert not e._inside_consolidation(4.07,50.,60.)
    e.approach[0]=(30,4.05,4.1,4.04)
    e.approach[10]=(40,4.05,4.06,4.)
    assert e._inside_consolidation(4.07,50.,60.)


def test_retirement_is_only_for_carried_levels():
    for carried in (False, True):
        e=bracket(10.,11.)
        if carried:
            e.persisted_ids.add(1)
        level=e.active[1]
        e._publish(level,3.,'accepted_break')
        e._publish(level,4.,'accepted_break')
        assert (1 in e.retired)==carried


def test_prefix_snapshot_is_not_rewritten_by_future_bars():
    e=bracket(4.,4.1)
    before=deepcopy(e.snapshot())
    e.observe(3.,4.5,4.4,4.45)
    assert before['unified_levels'][0]['confirmed_at_ms']==2000
