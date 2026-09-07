from functools import lru_cache
import random

import pytest

from src.market_engine.hindsight import HindsightOptimizer


def solve(rows, cost=5):
    model = HindsightOptimizer(cost)
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
