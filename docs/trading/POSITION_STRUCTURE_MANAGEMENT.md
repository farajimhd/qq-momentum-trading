# Position structure management

`episode_management.position_structure_enabled` opts the V5 MACD episode
rejection contract into `position-swings-1`. Existing candidates remain unchanged.

The entry policy is unchanged. Position state starts at the first observation of
held shares and survives MACD episode resets. Only complete one-second candles
entirely after that observation participate. Missing candles reset the bounded
confirmation window; duplicate closes and quotes cannot confirm pivots.

A swing high/low must strictly exceed the corresponding extremes of the
configured left and right candles. An outside candle that qualifies as both is
ambiguous and is not accepted. Confirmation occurs when the last right candle
closes, never at the earlier pivot time. The reversal must exceed the greater of
the configured price buffer and prior completed ATR buffer.

A confirmed internal high forms a resistance. A completed candle contacting a
qualified V5 resistance from below can also establish an attempt. A discretionary
resistance-failure exit needs consecutive closes below the buffered, previously
confirmed pullback low associated with that resistance. A touch or ordinary
pullback above that support is not an exit. A strict completed upper-band break
resolves the resistance and establishes the latest confirmed internal low as
support; established support can advance but cannot move down. Breaking it also
permits a discretionary exit. The initial and V5 structural broker stops and
session safeguards remain authoritative independently of these conditions.

The policy rejects an independent ATR profit trail and a target fraction below
one. Entry must produce a full-quantity protection slice containing a stop and
an overhead target. The target starts just below the second qualified overhead
V5 resistance. Existing causal resistance-gap progression advances it on
completed breaks; no valid higher target means keeping the current target.
Once a target starts filling, it is no longer advanced away from execution.

OMS marks these entries with `mandatory_broker_target`. Inactive children do not
count as active partial-position protection. An active OCA repair pair covers
acquired shares, both quantities grow with subsequent fills, and excess pairs
are retired when the parent bracket activates. Existing protection remains
working during price amendments; broker rejection does not advance the repair
profile or effective-price journal. Repair identities are persisted immediately,
and recovery rehydrates protection-event deduplication from the journal.

Configuration used for the first opt-in candidate:

```json
{
  "position_structure_enabled": true,
  "swing_left_bars": 2,
  "swing_right_bars": 2,
  "swing_buffer_bps": 5,
  "swing_buffer_atr_multiple": 0.25,
  "rejection_closes": 2,
  "profit_trail_atr_multiple": 0,
  "rejection_buffer_requires_armed_trail": false,
  "take_profit_fraction": 1
}
```

These are configurable engineering defaults, not fitted ticker/date rules.
An additional opt-in setting, `same_episode_reentry_stop`, replaces the initial
stop on a subsequent acquisition in the same MACD episode. Its anchor is the
maximum of the prior observed episode prices and completed candle opens/closes.
The current decision price is excluded from the observed-price anchor. MACD
continues to use completed one-second samples; price tracking runs independently.
At a candle-close decision the current candle body is excluded; at a later
intrabar execution it is already complete and is included.
After a target fill, a completed one-second candle strictly newer than that fill
is required before re-entry, including a new confirmation when confirmation
windows are enabled. This gate uses persisted fill evidence, survives recovery,
and also applies if MACD resets before the next entry.
`reentry_stop_offset_bps` defaults to 5; the stop is at least one tick
below that anchor and rounds downward to a valid tick. The entry's separate
15-bps clearance requirement is unchanged. Only an actual entry fill marks an
episode as acquired; unfilled or rejected requests do not. Holding a position
into a new qualifying episode also marks that episode as occupied, so exiting
and entering again there uses the re-entry stop. A new episode observed while
flat retains the normal first-entry stop rule. The selected anchor is
frozen through partial acquisition and is not rebased to a later average fill.

Position swing history contains at most `left + right + 1` bars (maximum 121),
plus the latest pivot, resistance and support witnesses. No new historical data
source or global level-book writer is introduced.

## Adaptive episode targets (separate opt-in version)

`adaptive_target_enabled` requires position structure and its full-position
broker target. It is off for existing profiles. The new profile
`swing-v5-adaptive-episode-target-v1` derives from Candidate 173, preserving its
entry, stop, re-entry, execution and structural-exit rules.

Only completed one-second candles contribute statistics. Each MACD episode
starts fresh. A rolling mean of positive candle bodies (bearish/doji candles
contribute zero) measures bullish expansion. Every confirmed resistance break
contributes its next two adjacent lower-bound gaps, deduplicated within a bounded
window. Before any break, initial selection uses the available overhead gaps.

The desired price is the nearest overhead resistance's lower bound plus
`max(mean_gap * gap_multiple, mean_bullish_body * body_multiple)`. Select the
first eligible resistance whose executable target price meets that distance,
at least the existing initial target ordinal. If none reaches it, use the
highest eligible resistance and record `book_limited=true`; do not invent a
price level or remove the broker target. Selected evidence records both means,
sample counts, desired price, episode and statistics timestamp.

Defaults are 8 body samples, 32 gap samples, body multiple 2, gap multiple 1,
contraction ratio 0.5 and 2 exhaustion closes. All are configurable and are
engineering defaults, not fitted backtest results. Two contracting closes
(each below half the preceding rolling mean) or two closes stalled below the
same forming resistance pause target advancement. Renewed expansion and a
resolved resistance can resume it. Gaps in candle time reset consecutive counts.

All breaks at one close produce one upward-only target proposal. Existing
stop ratchets and structural failure exits continue during a pause; contraction
alone creates no sell instruction. A broker target remains working through the
existing replacement/acknowledgement path. A target already filled intrabar
cannot be retrospectively moved using that candle's final size. Each sample
window is bounded to at most 120 entries and persists with the strategy state.

Focused tests cover causal pivot confirmation, ordinary pullbacks, strict
breaks, failed support, missing candles, MACD reset continuity, restart state,
full-target entry construction, partial acquisition and bracket activation,
rejected amendments, and repaired-order recovery. Simulator checks do not
certify broker behavior during an actual network outage or establish strategy
profitability. Live promotion requires separate broker acceptance evidence.
