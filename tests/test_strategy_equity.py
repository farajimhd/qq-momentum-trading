import pytest
from scripts.measure_strategy_equity import measure, stamp


def fill(identity, second, side, quantity, price, fee=1):
    return dict(execution_id=identity, source_event_time=f'2026-01-01T00:00:{second:02d}Z',
                side=side, quantity=str(quantity), price=str(price), commission=str(fee))


def test_equity_slices_preserve_carried_inventory_and_boundary_fees():
    base = stamp('2026-01-01T00:00:00Z')
    times = [base+i for i in range(5)]
    prices = [10, 10, 12, 11, 10]
    executions = [fill('entry', 1, 'BUY', 100, 10),
                  fill('partial', 2, 'SELL', 40, 12), fill('exit', 4, 'SELL', 60, 10)]
    whole = measure(executions, times, prices, base, base+4, include_end=True)
    assert whole['net_marked_pnl'] == pytest.approx(77)
    assert whole['maximum_drawdown'] == pytest.approx(121)
    assert whole['closing_quantity'] == 0
    assert whole['fees'] == 3
    early = measure(executions, times, prices, base, base+2)
    late = measure(executions, times, prices, base+2, base+4, include_end=True)
    assert early['closing_quantity'] == late['opening_quantity'] == 100
    assert early['net_marked_pnl']+late['net_marked_pnl'] == pytest.approx(whole['net_marked_pnl'])
    assert early['fees']+late['fees'] == whole['fees']
    assert late['execution_count'] == 2


def test_equity_never_uses_future_mark_for_held_inventory():
    base = stamp('2026-01-01T00:00:00Z')
    executions = [fill('entry', 1, 'BUY', 100, 10)]
    with pytest.raises(ValueError, match='causal trade mark'):
        measure(executions, [base+2], [12], base, base+3)
    report = measure(executions, [base], [10], base, base+30)
    assert report['closing_mark_age_seconds'] == 30
    assert report['net_marked_pnl'] == -1


def test_equity_rejects_duplicate_fills_and_unordered_marks():
    base = stamp('2026-01-01T00:00:00Z')
    entry = fill('entry', 1, 'BUY', 100, 10)
    with pytest.raises(ValueError, match='Duplicate execution'):
        measure([entry, entry], [base], [10], base, base+3)
    with pytest.raises(ValueError, match='ordered'):
        measure([entry], [base+1, base], [10, 10], base, base+3)
