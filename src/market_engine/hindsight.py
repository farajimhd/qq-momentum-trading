"""Idealized, full-information long-only benchmark. Never an execution signal.

Maximize additive net dollars for one share, with unlimited sequential round trips,
observed ask/bid fills and proportional cost per side. The two-state dynamic
program is O(quotes); retained state contains trade backpointers, not raw events.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import quantiles
from bisect import bisect_right


@dataclass(frozen=True, slots=True)
class Position:
    entry_time: float
    exit_time: float
    entry_price: float
    exit_price: float
    net_profit: float
    previous: Position | None
    count: int


@dataclass(frozen=True, slots=True)
class Holding:
    value: float
    time: float
    ask: float
    previous: Position | None


class HindsightOptimizer:
    def __init__(self, cost_bps: float = 5.0, max_spread_bps: float = 100.0):
        if not isfinite(cost_bps) or not 0 <= cost_bps <= 1000:
            raise ValueError("Cost per side must be between 0 and 1000 bps")
        self.cost = cost_bps / 10_000
        if not isfinite(max_spread_bps) or not 0 <= max_spread_bps <= 10_000:
            raise ValueError("Maximum spread must be between 0 and 10000 bps")
        self.max_spread_bps = max_spread_bps
        self.cash = 0.0
        self.path: Position | None = None
        self.holding: Holding | None = None
        self.timestamp: float | None = None
        self.base_cash = 0.0
        self.base_path: Position | None = None
        self.base_holding: Holding | None = None
        self.quotes = 0
        self.rejected_quotes = 0
        self.rejection_reasons = {"non_finite": 0, "non_positive_price": 0, "crossed": 0, "no_liquidity": 0, "wide_spread": 0}

    def observe(self, timestamp: float, bid: float, ask: float,
                bid_size: float, ask_size: float) -> None:
        if not isfinite(timestamp) or (self.timestamp is not None and timestamp < self.timestamp):
            raise ValueError("Quotes must be ordered by finite source timestamps")
        if timestamp != self.timestamp:
            # Freeze the preceding timestamp's states: neither a zero-duration
            # trade nor an exit/reentry at the identical timestamp is permitted.
            self.base_cash, self.base_path, self.base_holding = self.cash, self.path, self.holding
            self.timestamp = timestamp
        self.quotes += 1
        reason = ("non_finite" if not all(isfinite(x) for x in (bid, ask, bid_size, ask_size))
                  else "non_positive_price" if bid <= 0 or ask <= 0
                  else "crossed" if bid > ask
                  else "no_liquidity" if bid_size < 1 or ask_size < 1
                  else "wide_spread" if (ask - bid) / ((ask + bid) / 2) * 10_000 > self.max_spread_bps + 1e-10 else None)
        if reason:
            self.rejected_quotes += 1
            self.rejection_reasons[reason] += 1
            return
        if self.base_holding is not None:
            h = self.base_holding
            value = h.value + bid * (1 - self.cost)
            if value > self.cash + 1e-10:
                profit = bid * (1 - self.cost) - h.ask * (1 + self.cost)
                self.cash = value
                self.path = Position(h.time, timestamp, h.ask, bid, profit, h.previous,
                                     (h.previous.count if h.previous else 0) + 1)
        value = self.base_cash - ask * (1 + self.cost)
        if self.holding is None or value > self.holding.value + 1e-10:
            self.holding = Holding(value, timestamp, ask, self.base_path)

    def result(self) -> dict:
        positions = []
        node = self.path
        while node is not None:
            positions.append({
                "entry_time": node.entry_time, "exit_time": node.exit_time,
                "entry_price": node.entry_price, "exit_price": node.exit_price,
                "net_profit_per_share": node.net_profit,
                "net_return_bps": node.net_profit / (node.entry_price * (1 + self.cost)) * 10_000,
            })
            node = node.previous
        positions.reverse()
        for index, position in enumerate(positions):
            position["position_number"] = index + 1
        return {"positions": positions, "net_profit_per_share": self.cash,
                "quotes": self.quotes, "rejected_quotes": self.rejected_quotes,
                "rejection_reasons": dict(self.rejection_reasons),
                "position_count": len(positions)}


def small_profit_filter(positions: list[dict]) -> dict:
    """Descriptive lower-quartile screen; never claim this is a new optimum.

    Keep ties at the boundary. Fewer than four trades do not support a useful
    quartile screen and are retained. The raw oracle sequence remains available.
    """
    cutoff = quantiles([p["net_return_bps"] for p in positions], n=4, method="inclusive")[0] if len(positions) >= 4 else 0.0
    kept = [p for p in positions if p["net_return_bps"] >= cutoff]
    return {"method": "net-return-lower-quartile-v1", "percentile": 25,
            "cutoff_bps": cutoff, "retained_count": len(kept),
            "removed_count": len(positions) - len(kept),
            "retained_profit_per_share": sum(p["net_profit_per_share"] for p in kept)}


def merge_positions(positions: list[dict], bullish_intervals: list[tuple[float, float]], cost_bps: float = 5) -> list[dict]:
    """Merge adjacent long trades, preserving raw rows and recomputing endpoint P&L."""
    starts = [start for start, _ in bullish_intervals]
    def episode(p):
        index = bisect_right(starts, p["entry_time"]) - 1
        return index if index >= 0 and p["exit_time"] < bullish_intervals[index][1] else -1
    fee = cost_bps / 10_000
    result = []
    previous_episode = -1
    for position in positions:
        current_episode = episode(position)
        row = {**position, "component_positions": [position["position_number"]]}
        if result:
            previous = result[-1]
            gap = position["entry_time"] - previous["exit_time"]
            same_episode = current_episode >= 0 and current_episode == previous_episode
            profit = position["exit_price"] * (1 - fee) - previous["entry_price"] * (1 + fee)
            if (0 < gap <= 1 or same_episode) and profit > 1e-10:
                previous["component_positions"].append(position["position_number"])
                previous.update(exit_time=position["exit_time"], exit_price=position["exit_price"],
                    net_profit_per_share=profit,
                    net_return_bps=profit / (previous["entry_price"] * (1 + fee)) * 10_000)
            else:
                result.append(row)
        else:
            result.append(row)
        previous_episode = current_episode
    return result
