"""Idealized, full-information long-only benchmark. Never an execution signal.

Maximize additive net dollars for one share, with unlimited sequential round trips,
observed ask/bid fills and proportional cost per side. The two-state dynamic
program is O(quotes); retained state contains trade backpointers, not raw events.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


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
    def __init__(self, cost_bps: float = 5.0):
        if not isfinite(cost_bps) or not 0 <= cost_bps <= 1000:
            raise ValueError("Cost per side must be between 0 and 1000 bps")
        self.cost = cost_bps / 10_000
        self.cash = 0.0
        self.path: Position | None = None
        self.holding: Holding | None = None
        self.timestamp: float | None = None
        self.base_cash = 0.0
        self.base_path: Position | None = None
        self.base_holding: Holding | None = None
        self.quotes = 0
        self.rejected_quotes = 0
        self.rejection_reasons = {"non_finite": 0, "non_positive_price": 0, "crossed": 0, "empty_size": 0}

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
                  else "empty_size" if bid_size <= 0 or ask_size <= 0 else None)
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
        return {"positions": positions, "net_profit_per_share": self.cash,
                "quotes": self.quotes, "rejected_quotes": self.rejected_quotes,
                "rejection_reasons": dict(self.rejection_reasons),
                "position_count": len(positions)}
