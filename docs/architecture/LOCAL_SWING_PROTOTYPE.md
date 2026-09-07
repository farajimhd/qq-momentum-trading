# Local swing management and event-driven entry prototype

Opt in with `local_swing_management` on a new Test Candidate. Previous candidates
retain their behavior. This is a historical prototype, not a live promotion.

## Entry

The canonical trade/quote stream provides 100ms price buckets. On each eligible
trade, compare price with the high of the preceding one-second range, excluding
the current bucket. Require at least three prior trades spanning 200ms, a fresh
snapshot and quote, and price strictly above that high plus one tick. The latest
trade must rise relative to the prior strategy observation; a falling tick or a
completed-bar callback cannot trigger this entry.

The breakout signal lasts 500ms. An expired breakout above the same boundary
cannot renew while the preceding range is expanding; it needs a new tight base
(range <= max(4 ticks, 10bps)) or a reset below the old boundary. Reject extension
from the base low greater than max(3 base widths, 80bps). These initial constants
are hypotheses, not fitted performance claims.

The new candidate uses positive forming 1s MACD with MACD minus signal > 0.5bps,
including reentry. It keeps current R3 upper-bound acceptance, level quality,
spread/liquidity, Portfolio and OMS gates. Local upward breakout acceptance
replaces the enclosing 1s candle's color requirement. No new subsecond indicator
warm-up or completed-candle wait is introduced. Adverse usable pressure vetoes
entry; unclassified flow alone does not veto an otherwise valid price breakout.
Fresh quote/range data is still required. There is no prediction of range
direction before breakout, and downward breaks do not authorize long entries.

## Protection and exit

Initial protection is one tick below the causal local base low. Position state
tracks a running high, a pullback low, and a rally back toward the prior high.
The reversal distance is fixed when management first sees the position:
max(2 ticks, min(1.5 spreads, 75bps), min(quarter of local range, 75bps)).

A drop by that distance confirms a peak and starts a pullback. A rebound by the
distance confirms the pullback low at the rebound's timestamp, never retroactively.
If higher than the protected low, it raises the stop to one tick below that low.
Protection never loosens. Breaking above the previous high plus one tick starts
another upward leg. An ordinary pullback is held while protection remains intact.

A rally that fails below/near the prior high and reverses by the same distance
can exit early when usable executed-trade imbalance <= -0.30 and quote imbalance
< 0 agree. Without that evidence, the hard stop remains the exit authority.
Session flatten and manual exit retain priority. Standalone MACD/pressure exits,
fixed profit targets, target replacement, and target-room entry waits are removed
from this opt-in mode so they do not contradict swing management.

Reentry requires the prior exit to finish and a new local breakout dated after
that exit, plus the usual entry gates. There is no fixed cooldown. The approach
cannot identify the exact top causally and may return profit while waiting for
confirmation. It needs comparative testing across trends, chop, gaps and spreads.

`micro_entry` and `local_swing` decision metadata expose boundaries, phase,
confirmation timestamps and protection. Swing state is bounded and carried by
the existing assignment checkpoint; raw events are not retained. The QMD live
feed still needs the pressure/micro-range producer before this candidate can
be used live.
