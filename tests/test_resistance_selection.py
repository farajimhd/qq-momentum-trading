from copy import deepcopy
from src.market_engine.resistance_selection import select_areas


def level(i=1, price=7.2, **kw):
    return dict(level_id=i, price=price, lower=price-.01, upper=price+.01,
                side='resistance', state='active', scale='major', pivot_at=1,
                confirmed_at=2, history_threshold=.2, best_departure=.2,
                independent_retests=2, role_retests=0, **kw)


def test_future_confirmation_and_inactive_not_selected():
    rows = [level(), dict(level(2), confirmed_at=11), dict(level(3), state='awaiting_retest')]
    before = deepcopy(rows)
    assert select_areas(rows, 10)[0]['members'] == ['1']
    assert rows == before


def test_duplicates_do_not_add_score_and_width_does_not_chain():
    single = select_areas([level()], 10)[0]['score']
    rows = [level(i, 7.2+i*.02) for i in range(5)]
    areas = select_areas(rows, 10, maximum_width_bps=100)
    assert len(areas) == 2
    assert all(a['score'] == single for a in areas)
    assert all((a['upper']-a['lower'])/a['lower']*10000 <= 100 for a in areas)


def test_new_role_cannot_inherit_support_confidence():
    row = level(last_role_change_at=9)
    assert select_areas([row], 10)[0]['score'] == 20
    assert not select_areas([row], 10)[0]['selected']
    row['role_retests'] = 1
    assert select_areas([row], 10)[0]['selected']


def test_crossings_reduce_grade_and_high_prices_not_filtered():
    a = level(price=12.)
    assert select_areas([a], 10)[0]['price'] == 12
    b = dict(a, accepted_crossings=1)
    assert select_areas([b], 10)[0]['score'] < select_areas([a], 10)[0]['score']


def test_service_emits_evidence_changes_without_geometry_revision(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from src.backend import resistance_selection_service as service
    opening = datetime.fromisoformat('2026-08-21T04:00:00-04:00').timestamp()
    class Cursor:
        def __init__(self, *args):
            self.build = dict(version='causal-swing-closing-book-4', fingerprint='test')
            self.bars = [(opening+1, 7.2, 7.1, 7.1), (opening+2, 7.2, 7., 7.)]
            self.engine = SimpleNamespace(active={}, revision=0)
        def advance(self, at):
            r = dict(level(), independent_retests=0, best_departure=.1 if at.timestamp() < opening+2 else .2)
            self.engine.active = {1:r}
    monkeypatch.setattr(service, 'SwingBookCursor', Cursor)
    result = service.calculate(service.SelectionRequest(ticker='JUNS', session_date='2026-08-21', book_id='test'))
    before, after = result['segments']
    assert before['score'] == 20 and not before['selected']
    assert before['valid_from'] == opening and before['valid_to'] == opening+2
    assert after['score'] == 40 and after['selected'] and after['valid_from'] == opening+2
