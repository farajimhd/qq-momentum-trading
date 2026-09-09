# Candidate 180: V6 structural support recovery

Independent trading policy `v6-structural-recovery-1`, dispatched through the shared
Strategy executor, Portfolio risk sizing and OMS protected-order path. It does not
use Candidate 179, MACD episodes, VWAP clearance or episode-high entry gates.

## Test in Backtest

Select Candidate **180 — V6 structural support recovery**, its matching Run Plan,
one ticker, and that ticker's certified **V6** experimental swing book. Missing V6,
wrong book version, mismatched ticker or uncovered dates fail preflight. Candidate
creation uses `scripts/create_structural_recovery_candidate.py --revision 180`; it refuses to
overwrite an occupied candidate number. No live or paper deployment is created.

## Rules

- Observe completed 1s candles continuously, including before discovery and while
  flat. Use `StructuralDetector` v4 with its default settings, pinned V6 book identity
  and causal level snapshots. After a session boundary, missing candle or book
  change, reset the observation context and require 30 candles of warmup.
- A V6 support test/rejection, or successful retest of broken V6 resistance,
  establishes a setup. A **later completed candle** must close above the frozen
  support-test candle high and support upper boundary, with detector direction
  bullish and movement state advance/recovery. The test-candle high is the explicit
  recovery trigger; it is not claimed to be a separately confirmed swing high.
- Setup lifetime is 120 seconds. A support failure invalidates it. Confirmation
  expires after one second, and each confirmation can authorize only one entry.
- Require $2–$50 price, at least $1M session dollar volume, 100,000 session shares,
  5 trades/second over both 10s and 60s, and spread at most 60 bps. Require uncrossed
  positive quotes at most 1s old, activity facts at most 2s old, and positive volume
  on the completed signal candle. Tradability discovery does not latch entry
  permission: the strategy rechecks current evidence before acquisition.
- Stop below the lower of the support zone and observed correction low, buffered
  by the greater of one tick and 5 bps. Never loosen that stop after acquisition.
- Full-position broker target one tick below the nearest overhead V6 resistance.
  Require net reward/risk at least 1.5 after a 5-bps-per-side cost allowance.
  Buy ceiling also limits chase to 15 bps beyond the confirming close. OMS receives
  that ceiling and the remaining one-second acquisition deadline.
- Request risk sizing at 0.5% of available broker cash, maximum 10,000 shares,
  subject to Portfolio capital/exposure/liquidity limits. No strategy adds.
- Hold ordinary pullbacks; exit on a fresh completed close below the entry support,
  a protective-stop breach, or session flatten. Cancel remaining acquisition when
  tradability, confirmation age or the buy ceiling fails; retain broker protection
  for acquired shares. Pending exits prevent another entry.
- Premarket/regular sessions; no new entries from 15:45 New York; flatten at 15:55.
  Reentry has no cooldown and needs an unconsumed, currently valid recovery setup.

## Evidence and scope

Decisions carry detector state/progression/volume, exact support/setup timestamps,
book fingerprint, tradability facts/checks, stop/target and acquisition ceiling.
Typed JSON detector checkpoints preserve deque limits, tuple keys, counters and
engine version without executable serialization. A restart uses the same state.

This first policy uses a fixed structural stop and next-resistance target. Volume
divergence and progression remain visible evidence; they do not automatically
extend targets, tighten stops or imply reversal probabilities. HOD is not an entry
requirement. Profitability and parameter robustness require the user's backtests.

## Candidate 181: admission and execution gates aligned with 179

Candidate 181 retains the independent structural entry/exit policy above. It
changes only tradability to a two-stage policy based on saved Candidate 179:

- Admission: $2–$50, $1M session dollar volume, 100,000 session shares,
  at least 1 trade/second over 10s and 0.5 over 60s, spread at most 60 bps.
- Admission is remembered for the current exchange session. It does not authorize
  an entry by itself: every acquisition still requires current trade rates of at
  least 5/second over both 10s and 60s, and spread at most 100 bps.
  The user chose to retain this stricter 100-bps limit after comparison with
  Candidate 179's selected strategy, which allows 200 bps for current execution.
- The structural strategy retains its quote/activity freshness checks, positive
  signal-candle volume, structural confirmation expiry, buy ceiling and protected
  execution. No MACD/VWAP entry condition is imported from 179.

Create or verify 181 with `scripts/create_structural_recovery_candidate.py`.
Original Candidate 180 remains unchanged for reproducible comparisons.
Backtest automatically selects the date-covered V6 book for the chosen ticker,
including custom ticker input and each ticker in batch presets. A ticker/date
change clears a stale book selection; the structural strategy cannot launch
without a matching V6 book.
