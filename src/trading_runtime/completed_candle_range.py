"""Causal completed-candle highs independent of strategy entry eligibility."""
from collections import deque
from math import isfinite

CONTRACT = 'completed-candle-range-1'


class CompletedCandleRange:
    def __init__(self, seconds, coverage_start):
        if not isfinite(seconds) or not 0 < seconds <= 3600 or not isfinite(coverage_start):
            raise ValueError('Completed candle range requires a bounded duration and coverage start')
        self.seconds = float(seconds)
        self.coverage_start = float(coverage_start)
        self.rows = deque()

    def observe(self, at, high):
        if not all(isfinite(v) for v in (at, high)) or high <= 0 or at < self.coverage_start:
            raise ValueError('Completed candle range received an invalid candle')
        if self.rows and at <= self.rows[-1][0]:
            if at == self.rows[-1][0] and high == self.rows[-1][1]:
                return
            raise ValueError('Completed candle history is unordered or conflicting')
        self.rows.append((float(at), float(high)))
        while self.rows and self.rows[0][0] < at-self.seconds:
            self.rows.popleft()

    def context(self, at):
        if not self.rows or self.rows[-1][0] != at:
            return dict(contract=CONTRACT, ready=False, as_of=at, seconds=self.seconds)
        prior = [row for row in self.rows if at-self.seconds <= row[0] < at]
        return dict(contract=CONTRACT, ready=True, as_of=at, seconds=self.seconds,
                    coverage_start=self.coverage_start,
                    window_start=max(self.coverage_start, at-self.seconds),
                    high=max((row[1] for row in prior), default=None), samples=len(prior))

    def checkpoint(self):
        return dict(seconds=self.seconds, coverage_start=self.coverage_start, rows=list(self.rows))

    @classmethod
    def restore(cls, value):
        result = cls(float(value['seconds']), float(value['coverage_start']))
        previous = float('-inf')
        for at, high in value['rows']:
            if float(at) <= previous:
                raise ValueError('Completed candle checkpoint has duplicate or unordered rows')
            result.observe(float(at), float(high))
            previous = float(at)
        return result
