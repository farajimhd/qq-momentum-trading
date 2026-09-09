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
prior maximum of completed candle opens/closes. At a candle-close decision the
current candle is excluded; at a later intrabar execution it is already complete
and is included. `reentry_stop_offset_bps` defaults to 5; the stop is at least one tick
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

Focused tests cover causal pivot confirmation, ordinary pullbacks, strict
breaks, failed support, missing candles, MACD reset continuity, restart state,
full-target entry construction, partial acquisition and bracket activation,
rejected amendments, and repaired-order recovery. Simulator checks do not
certify broker behavior during an actual network outage or establish strategy
profitability. Live promotion requires separate broker acceptance evidence.
