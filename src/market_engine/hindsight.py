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
from bisect import bisect_left
from array import array
from collections import Counter, deque
from datetime import datetime
from types import SimpleNamespace


class LiquidMacdBenchmark:
    """Observed quote candidates gated by preceding canonical trade activity.

    Compact columns bound quote memory to 24 bytes per accepted event. No quote
    is forward filled: each candidate is the published NBBO at its own timestamp.
    """
    def __init__(self, *, cost_bps=5, max_spread_bps=100, min_displayed_shares=100,
                 activity_window_seconds=1, min_trade_count=3, min_trade_volume=100):
        self.fee = cost_bps / 10000
        self.spread = max_spread_bps
        self.depth = min_displayed_shares
        self.window = activity_window_seconds
        self.min_count = min_trade_count
        self.min_volume = min_trade_volume
        self.trades = deque()
        self.volume = 0.0
        self.times, self.bids, self.asks = array('d'), array('d'), array('d')
        self.reasons = Counter()
        self.quotes = self.trade_count = self.invalid_trades = self.events = 0
        self.last_time = float('-inf')

    def observe(self, event):
        t = event.ts.timestamp()
        if not isfinite(t) or t < self.last_time:
            raise ValueError('Events must have ordered finite source timestamps')
        self.last_time = t
        self.events += 1
        if self.events > 10_000_000:
            raise RuntimeError('Session exceeds the 10-million-event research budget')
        while self.trades and self.trades[0][0] <= t - self.window:
            self.volume -= self.trades.popleft()[1]
        if event.kind == 'trade':
            self.trade_count += 1
            if not event.price_eligible or not isfinite(event.size) or not isfinite(event.price) or event.size <= 0 or event.price <= 0:
                self.invalid_trades += 1
                return
            self.trades.append((t, event.size))
            self.volume += event.size
            if len(self.trades) > 1_000_000:
                raise RuntimeError('Trade activity window exceeds research memory budget')
            return
        if event.kind != 'quote':
            raise ValueError('Unsupported canonical event kind')
        self.quotes += 1
        b, a, bs, az = event.bid_price, event.ask_price, event.bid_size, event.ask_size
        reason = ('non_finite' if not all(isfinite(x) for x in (b, a, bs, az))
                  else 'non_positive_price' if min(b, a) <= 0
                  else 'crossed' if b > a
                  else 'no_liquidity' if min(bs, az) < self.depth
                  else 'wide_spread' if (a-b)/((a+b)/2)*10000 > self.spread + 1e-10
                  else 'insufficient_trade_count' if len(self.trades) < self.min_count
                  else 'insufficient_trade_volume' if self.volume < self.min_volume else None)
        if reason:
            self.reasons[reason] += 1
            return
        self.times.append(t)
        self.bids.append(b)
        self.asks.append(a)

    def observe_payload(self, row):
        """Project only fields used here; avoid rich execution-event hydration."""
        common = dict(kind=row['kind'], ts=datetime.fromisoformat(row['ts'].replace('Z', '+00:00')))
        if row['kind'] == 'trade':
            event = SimpleNamespace(**common, price=float(row.get('price') or 0), size=float(row.get('size') or 0),
                                    price_eligible=(row.get('raw') or {}).get('price_eligible') is not False)
        else:
            event = SimpleNamespace(**common, **{k: float(row.get(k) or 0) for k in ('bid_price', 'ask_price', 'bid_size', 'ask_size')})
        self.observe(event)

    def result(self, intervals, lookback_seconds=2):
        positions, rejected = [], Counter()
        previous_exit = float('-inf')
        for number, (start, end) in enumerate(intervals, 1):
            first = max(bisect_left(self.times, start-lookback_seconds), bisect_right(self.times, previous_exit))
            last = bisect_right(self.times, start)
            if first >= last:
                rejected['no_liquid_entry'] += 1
                continue
            buy = min(range(first, last), key=self.asks.__getitem__)
            first_sell = max(bisect_left(self.times, start), bisect_right(self.times, self.times[buy]))
            last_sell = bisect_left(self.times, end)
            if first_sell >= last_sell:
                rejected['no_liquid_exit'] += 1
                continue
            sell = max(range(first_sell, last_sell), key=self.bids.__getitem__)
            profit = self.bids[sell]*(1-self.fee) - self.asks[buy]*(1+self.fee)
            if profit <= 1e-10:
                rejected['non_positive_net_profit'] += 1
                continue
            positions.append(dict(position_number=number, entry_time=self.times[buy], exit_time=self.times[sell],
                entry_price=self.asks[buy], exit_price=self.bids[sell], net_profit_per_share=profit,
                net_return_bps=profit/(self.asks[buy]*(1+self.fee))*10000, macd_open=start, macd_close=end))
            previous_exit = self.times[sell]
        return dict(positions=positions, position_count=len(positions), interval_count=len(intervals),
                    interval_rejections=dict(rejected), quotes=self.quotes, trades=self.trade_count,
                    invalid_trades=self.invalid_trades, eligible_quotes=len(self.times),
                    rejected_quotes=sum(self.reasons.values()), rejection_reasons=dict(self.reasons),
                    net_profit_per_share=sum(p['net_profit_per_share'] for p in positions))


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
    quartile screen; the 500 bps minimum still applies. The raw oracle sequence
    remains available.
    """
    cutoff = quantiles([p["net_return_bps"] for p in positions], n=4, method="inclusive")[0] if len(positions) >= 4 else 0.0
    cutoff = max(500.0, cutoff)
    kept = [p for p in positions if p["net_return_bps"] >= cutoff]
    return {"method": "net-return-lower-quartile-floor-v2", "percentile": 25, "minimum_bps": 500.0,
            "cutoff_bps": cutoff, "retained_count": len(kept),
            "removed_count": len(positions) - len(kept),
            "retained_profit_per_share": sum(p["net_profit_per_share"] for p in kept)}


def merge_positions(positions: list[dict], bullish_intervals: list[tuple[float, float]], cost_bps: float = 5) -> list[dict]:
    """Group adjacent trades, then exit at the group's highest eligible swing bid.

    The optimal component exits already maximize eligible bids between their
    entries and the following entries. Their maximum is the merged window peak.
    Keep grouping independent of the shortened exit to avoid changing gap rules.
    """
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
    by_number = {p["position_number"]: p for p in positions}
    for row in result:
        row["merge_window_end"] = row["exit_time"]
        peak = max((by_number[n] for n in row["component_positions"]), key=lambda p: p["exit_price"])
        profit = peak["exit_price"] * (1 - fee) - row["entry_price"] * (1 + fee)
        row.update(exit_time=peak["exit_time"], exit_price=peak["exit_price"],
                   exit_component_position=peak["position_number"],
                   net_profit_per_share=profit,
                   net_return_bps=profit / (row["entry_price"] * (1 + fee)) * 10_000)
    return result
