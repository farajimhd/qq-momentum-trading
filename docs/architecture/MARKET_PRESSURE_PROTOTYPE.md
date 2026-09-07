# Causal market pressure prototype

Contract: `trade-nbbo-pressure-v1`. Opt in with the strategy profile's
`market_pressure.enabled`; existing candidates remain unchanged.

Backtest/Replay observes every canonical trade and NBBO quote before passive
event coalescing. It keeps at most 31 100-ms buckets per ticker, including in
restart checkpoints. No raw trade/quote evidence is retained by this feature.
Snapshots use trailing 1s (decision) and 3s (context) windows; partial left-edge
buckets are excluded, shortening coverage by at most 100ms. Decisions occur on
the existing trade and completed-indicator clocks, not on quote updates alone.

Trades at/above the preceding ask count as buying; trades at/below its bid count
as selling. Inside-spread trades remain unknown. The preceding quote must be
uncrossed, unlocked, and at most 1s old. Ineligible trades do not contribute.
Out-of-order events are rejected and counted. A snapshot cannot precede its
latest consumed event. Snapshot age must be at most 250ms at the decision.

Trade imbalance is `(buy-sell)/(buy+sell)`. Quote imbalance uses signed best-quote
order-flow imbalance (price movement and size changes), normalized by summed
adjacent displayed depth and clipped to [-1,1]. This measures top-of-book
changes, not identifiable cancellations or full-book liquidity. Price progress
and retreat from the window high are measured in current spreads.

Defaults require 3 trades, 50% classified volume, and a valid quote transition.
An adverse fast window means either:

- Selling: trade imbalance <= -0.30, quote imbalance <= -0.20, and price
  retreat >= 1 spread.
- Absorption: trade imbalance >= +0.30, quote imbalance <= -0.20, price
  progress <= 0, and retreat >= 1 spread.

Entry retains all existing structural, green-forming-candle, MACD, liquidity,
Portfolio, and OMS checks. Pressure blocks entry when adverse or unavailable;
it does not introduce another completed-candle wait.

Exit requires adverse evidence for >= 200ms with no gap between evaluations
greater than 250ms. It submits a full exit through the existing exit authority,
cancels remaining entry acquisition, and uses normal pending-fill handling.
Session flatten and protective stops take priority; MACD exits and fixed targets
remain independent. Missing pressure never forces an exit or suppresses a stop.

After a pressure exit, reentry additionally requires nonnegative trade and
quote imbalance and positive price progress. All normal entry conditions still
apply, and the previous exit must finish. Other exits do not add this recovery
requirement. There is no fixed cooldown.

Decision metadata includes `market_pressure`, component readings, readiness,
adverse/recovery flags, and reason. Parameters are prototype hypotheses; no
profitability or generalization claim follows from implementation tests.

The shared strategy observation accepts this contract from other producers.
The current live scanner adapter forwards an explicit `market_pressure` record,
but the live QMD feed does not yet produce it. Do not promote this test candidate
to live: absent evidence deliberately blocks new entries. A live producer and
live/historical parity validation remain separate work.
