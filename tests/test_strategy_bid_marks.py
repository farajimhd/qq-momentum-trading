import pytest
from scripts.benchmark_strategy_quotes import BidMarks


def quote(second, bid=10., ask=10.1, **fields):
    return dict(kind='quote', ticker='TEST', ts=f'2026-01-01T00:00:{second:02d}Z',
                ingest_ts=f'2026-01-01T00:00:{second:02d}Z', bid_price=bid, ask_price=ask,
                bid_size=100, **fields)


def test_bid_marks_count_rejections_and_preserve_causal_prices():
    marks = BidMarks('TEST')
    for row in [quote(0), quote(1, bid=11, ask=10), quote(2, bid=0),
                quote(3, bid=float('nan')), dict(quote(4, bid=9), bid_size=0), quote(4, bid=8)]:
        marks.observe(row)
    assert marks.prices == [10, 9, 8]
    assert marks.counts == dict(quotes=6, accepted=3, crossed_quote=1,
        nonpositive_bid=1, nonfinite_prices=1, accepted_without_displayed_bid_size=1)
    with pytest.raises(ValueError, match='causal timestamp order'):
        marks.observe(quote(3))


def test_bid_marks_fail_closed_on_wrong_symbol_or_gateway_error():
    marks = BidMarks('TEST')
    with pytest.raises(ValueError, match='requested symbol'):
        marks.observe(dict(quote(0), ticker='OTHER'))
    with pytest.raises(RuntimeError, match='historical stream failed'):
        marks.observe({'error': 'incomplete canonical coverage'})
