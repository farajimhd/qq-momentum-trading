from functools import lru_cache
import random

import pytest

from src.market_engine.hindsight import HindsightOptimizer


def solve(rows, cost=5):
    model = HindsightOptimizer(cost, max_spread_bps=10000)
    for timestamp, bid, ask in rows:
        model.observe(timestamp, bid, ask, 1, 1)
    return model.result()


def exhaustive(rows, cost):
    fee = cost / 10_000

    @lru_cache(None)
    def flat(start):
        best = 0.0
        for i in range(start, len(rows)):
            for j in range(i + 1, len(rows)):
                if rows[j][0] <= rows[i][0]:
                    continue
                next_start = next((k for k in range(j + 1, len(rows)) if rows[k][0] > rows[j][0]), len(rows))
                best = max(best, rows[j][1] * (1 - fee) - rows[i][2] * (1 + fee) + flat(next_start))
        return best
    return flat(0)


def test_matches_exhaustive_trade_sequences():
    rng = random.Random(94)
    for cost in (0, 5, 100):
        for _ in range(100):
            rows = [(i // 2, bid := rng.randint(100, 200) / 10, bid + rng.choice([0, .01, .2])) for i in range(12)]
            result = solve(rows, cost)
            assert result["net_profit_per_share"] == pytest.approx(exhaustive(rows, cost))
            assert sum(p["net_profit_per_share"] for p in result["positions"]) == pytest.approx(result["net_profit_per_share"])
            for p in result["positions"]:
                assert p["exit_time"] > p["entry_time"]
                assert p["net_profit_per_share"] > 0
            assert all(a["exit_time"] < b["entry_time"] for a, b in zip(result["positions"], result["positions"][1:]))


def test_costs_suppress_small_oscillations_and_never_leave_open_position():
    assert solve([(1, 10, 10.01), (2, 10.015, 10.02)])["positions"] == []
    assert solve([(1, 10, 10.01)])["positions"] == []
    assert solve([(1, 10, 10.01), (2, 9, 9.01)])["positions"] == []


def test_equal_profit_keeps_earlier_path():
    result = solve([(1, 1, 1), (2, 2, 2), (3, 2, 2)], 0)
    assert result["positions"][0]["exit_time"] == 2


def test_quote_rejections_and_order_validation():
    model = HindsightOptimizer()
    model.observe(1, 2, 1, 1, 1)
    model.observe(2, 1, 2, 0, 1)
    model.observe(3, float("nan"), 2, 1, 1)
    assert model.result()["rejected_quotes"] == 3
    with pytest.raises(ValueError, match="ordered"):
        model.observe(2, 1, 2, 1, 1)
    with pytest.raises(ValueError):
        HindsightOptimizer(float("nan"))


def test_hindsight_api_complete_session_contract(monkeypatch):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from src.backend import hindsight_service as service

    observed = {}
    class Source:
        source_revision = {"request_complete": True, "revision_token": "test"}
        def __init__(self, url, **kwargs):
            observed.update(kwargs)
        async def stream(self):
            yield SimpleNamespace(events=[SimpleNamespace(ts=datetime.fromisoformat(t), bid_price=b, ask_price=a, bid_size=1, ask_size=1)
                for t, b, a in [("2026-08-21T04:00:00-04:00", 5, 5.01), ("2026-08-21T19:59:59-04:00", 7, 7.01)]])
    monkeypatch.setattr(service, "QmdHistoricalEventSource", Source)
    result = asyncio.run(service.calculate(service.HindsightRequest(ticker="JUNS", session_date="2026-08-21")))
    assert observed["start"].hour == 4 and observed["end"].hour == 20
    assert observed["event_kinds"] == ("quote",)
    assert result["position_count"] == 1
    assert result["hindsight_only"] is True
    assert result["source_revision"]["revision_token"] == "test"


def test_source_error_does_not_publish_partial_optimum(monkeypatch):
    import asyncio
    from src.backend import hindsight_service as service
    class Source:
        def __init__(self, *args, **kwargs): pass
        async def stream(self):
            raise RuntimeError("coverage gap")
            yield
    monkeypatch.setattr(service, "QmdHistoricalEventSource", Source)
    with pytest.raises(RuntimeError, match="coverage gap"):
        asyncio.run(service.calculate(service.HindsightRequest(ticker="JUNS", session_date="2026-08-21")))


def test_wide_spread_and_absent_liquidity_cannot_supply_fills():
    model = HindsightOptimizer()
    model.observe(1, 9, 10, 1, 1)  # Wide, tempting cheap entry must be excluded.
    model.observe(2, 10, 10.01, .5, 1)  # Less than one share displayed.
    model.observe(3, 11, 11.01, 1, 1)
    model.observe(4, 20, 21, 1, 1)  # Wide, tempting exit must be excluded.
    model.observe(5, 12, 12.01, 1, 1)
    result = model.result()
    assert result["positions"][0]["entry_time"] == 3
    assert result["positions"][0]["exit_time"] == 5
    assert result["rejection_reasons"]["wide_spread"] == 2
    assert result["rejection_reasons"]["no_liquidity"] == 1
    boundary = HindsightOptimizer()
    boundary.observe(1, 99.5, 100.5, 1, 1)  # Exactly 100 bps is allowed.
    assert boundary.rejected_quotes == 0


def test_lower_quartile_filter_preserves_raw_and_ties():
    from src.market_engine.hindsight import small_profit_filter
    rows = [{"net_return_bps": x, "net_profit_per_share": x / 100} for x in [600, 800, 1000, 2000]]
    result = small_profit_filter(rows)
    assert result["cutoff_bps"] == 750
    assert result["removed_count"] == 1
    assert len(rows) == 4
    assert small_profit_filter(rows[:3])["removed_count"] == 0
    assert small_profit_filter([rows[0]] * 4)["removed_count"] == 0
    assert small_profit_filter([])["retained_count"] == 0
    floor_rows = [{"net_return_bps": x, "net_profit_per_share": 3 * x / 10000} for x in [499, 500, 501]]
    floor = small_profit_filter(floor_rows)
    assert floor["cutoff_bps"] == 500
    assert floor["removed_count"] == 1
    assert floor["retained_count"] == 2


def test_certified_session_without_liquidity_returns_zero_opportunities(monkeypatch):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from src.backend import hindsight_service as service
    class Source:
        source_revision = {"request_complete": True}
        def __init__(self, *args, **kwargs): pass
        async def stream(self):
            yield SimpleNamespace(events=[SimpleNamespace(ts=datetime.fromisoformat('2026-08-21T04:00:00-04:00'),
                bid_price=5, ask_price=5.01, bid_size=0, ask_size=0)])
    monkeypatch.setattr(service, 'QmdHistoricalEventSource', Source)
    result = asyncio.run(service.calculate(service.HindsightRequest(ticker='JUNS', session_date='2026-08-21')))
    assert result['position_count'] == 0
    assert result['rejection_reasons']['no_liquidity'] == 1
    assert result['profit_filter']['retained_count'] == 0


def test_merging_short_gaps_and_macd_episodes_reprices_endpoints():
    from src.market_engine.hindsight import merge_positions
    def p(i, start, end, buy, sell):
        return dict(position_number=i, entry_time=start, exit_time=end,
                    entry_price=buy, exit_price=sell, net_profit_per_share=sell-buy, net_return_bps=100)
    a, b = p(1, 1, 2, 10, 11), p(2, 2.5, 3, 10.8, 12)
    merged = merge_positions([a, b], [])
    assert len(merged) == 1
    assert merged[0]['net_profit_per_share'] == pytest.approx(12 * .9995 - 10 * 1.0005)
    assert merged[0]['component_positions'] == [1, 2]
    assert a['exit_time'] == 2 and 'component_positions' not in a
    b = p(2, 6, 7, 10.8, 12)
    assert len(merge_positions([a, b], [(0, 10)])) == 1
    assert len(merge_positions([a, b], [(0, 3), (5, 10)])) == 2
    assert len(merge_positions([a, p(2, 2.5, 3, 5, 6)], [])) == 2
    # MACD turns bearish at t=7: the second position is not fully inside the interval.
    assert len(merge_positions([a, b], [(0, 7)])) == 2


def test_macd_intervals_include_negative_values_and_stop_at_gaps(monkeypatch):
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from time import monotonic
    from src.backend import hindsight_service as service
    start = datetime.fromisoformat('2026-08-21T04:00:00-04:00')
    rows = [dict(bar_end=(start + timedelta(seconds=t)).isoformat(), macd_line=m, macd_signal=s)
            for t, m, s in [(1, -1, -2), (2, -.5, -1), (3, -2, -1), (4, .1, 0), (6, .2, .1)]]
    def read(request):
        assert request.timeframe == '1s' and request.stage == 'bars'
        return SimpleNamespace(payload={'indicators_available': True, 'indicator_provenance': {'complete': True},
                                        'has_more': False, 'indicators': rows})
    monkeypatch.setattr(service, 'qmd_product_request', read)
    intervals, _ = service.load_macd_intervals('SUGP', start, start + timedelta(seconds=10), lambda **kwargs: None, monotonic() + 10)
    t = start.timestamp()
    assert intervals == [(t+1, t+3), (t+4, t+5), (t+6, t+7)]
