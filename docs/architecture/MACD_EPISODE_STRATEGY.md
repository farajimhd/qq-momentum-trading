# MACD episode strategy

Contract `swing-v5-macd-episode-1` supersedes the rules of Candidate 118 for a
new test candidate. The earlier `swing-v5-macd-gap-1` executor path is retained;
an existing immutable candidate is not rewritten.

- Gap: `(MACD - signal) / price * 10000`, minimum 25 bps by default.
- Reset the episode high immediately on an intrabar gap below the minimum.
  Start at zero; update from completed 1s candle opens and closes. A candle
  completing at a decision boundary is included; forming-candle highs are not.
- Enter only above VWAP plus its buffer, at or above the episode body high,
  with the configured liquidity/spread/Portfolio gates satisfied. Cancel
  unfinished acquisition when the MACD or VWAP gate fails, without selling
  the held quantity.
- The initial stop uses the lower boundary of the latest available causal
  major support below entry, offset below the boundary. If unavailable, use
  the configured 5% entry distance. An available outer low is not capped by 5%.
- Only a completed close strictly crossing above a resistance's upper bound
  advances the stop below its lower bound. Trades and touches cannot advance
  it. Completed-bar witnesses are independent of intervening trade snapshots.
- Initially target the second overhead resistance. During each position,
  record adjacent lower-bound price gaps, once per level-ID pair. Seed from
  the highest broken resistance below acquisition when available. At a break,
  sample broken-to-next and next-to-second-next gaps. Let R1 be the next
  overhead resistance: choose a resistance above R1 nearest `R1 + average gap`;
  a distance tie selects the lower resistance. Never lower an existing target.
  Reset gap samples when flat. Missing overhead levels do not invent a target.
- Three-touch provisional resistance exits and urgent liquidation after a
  partial target fill are absent. The existing target continues serving the
  remaining position. Session/risk safeguards and OMS ownership remain.

Numeric policy lives in `v5_breakout` settings: `minimum_macd_gap_bps`,
`vwap_offset_bps`, `initial_stop_pct`, `initial_target_ordinal`,
`minimum_selection_score`, `stop_offset_bps`, and `target_offset_ticks`.

## Protection and evidence

OMS journals requested and broker-effective protection prices with causal time,
sequence, order identity and entry-root IDs. Position lifecycles receive events
through their opening executions' order IDs, never ticker-only matching.
Unlinked events are counted. The chart draws only effective changes as stepped
paths; stop and target controls govern their entire respective paths.

The SQLite journal stores structural evidence by SHA-256 and writes its
references and presentation projection transactionally. Recovery and detailed
reads hydrate and verify evidence; missing/corrupt evidence fails closed. Old
journals remain readable. No old run is silently rewritten. The incremental
activity index reads compact rows; full detail is loaded only when requested.

Runtime timing reports separate presentation, market/broker, strategy/execution,
and journal serialization/transaction measurements. Stage wall times are
inclusive and must not be added together as independent CPU measurements.
