# Staged V5 resistance breakout

Contract `swing-v5-staged-breakout-1` is separate from Candidate 110's policy.
V5 construction and qualification rules are unchanged.

Freeze the four highest qualified resistance bands below the current session
high at entry. Rank descending as R1, R2, R3, R4. Enter only after a fresh R4
upper-bound break while price remains below R3's lower bound. Retain bullish MACD,
VWAP, liquidity, volume, spread, previous completed 1s body and 400 ms direction
checks. The initial stop is 5% below the entry decision price, rounded down to a
tick; target is one tick below R2's lower bound. Actual fills may differ from
the decision price under the existing execution model.

On breaking R3, ratchet the stop below R4's lower bound and target the first
known resistance above R1 (one tick below its lower bound). If unavailable,
retain the existing target; never invent a boundary. After R1 breaks, wait for
a local top confirmed after that break. Newly created levels do not renumber
the frozen references.

Track completed absolute 1s candle bodies during the position, excluding the
partial entry candle. A completed bullish body crossing a previously available
local top must exceed the preceding body average to activate adaptive management.
Reset the average to this big candle as observation one, and accumulate from
there. Higher resistance breaks then advance protection and select the second
resistance beyond the broken upper bound plus this new average. Stops and targets
do not move down. Newly confirmed resistances inside the observed move may also
tighten protection during the adaptive phase. A stop already breached causes exit.

An active target remains executable throughout every stage. Reaching it before
a stage transition closes the position normally. Reentry uses the same entry
conditions. No new MACD exit or cooldown is introduced.

Chart presentation uses recorded frozen references and requested stop/target
changes. Initial protection lines end at the first replacement; replacement
segments extend to the next replacement or position end. These are strategy
instructions, not proof of broker acceptance or fills.

Validation uses focused synthetic strategy and chart fixtures. No backtest is
performed as part of this implementation.

## Continuous entry correction

Contract `swing-v5-staged-breakout-2` preserves Candidate 111 for comparison.
It removes the unrequested 250 ms observation-gap rejection and one-second
expiry of an R4 break. The 400 ms net upward price comparison remains required.
A previously observed crossing remains usable while price is above frozen R4
and below frozen R3 and the three upper references remain qualified. Returning
below R4, reaching R3, or losing those references invalidates it. A newly
appearing level cannot supply a retroactive crossing. All entry gates are
reevaluated using the current observation; no stale MACD or liquidity approval
is retained. The rank/crossing/invalidation evidence is included in decisions.

Audit of JUNS run `69934994-a349-4ee3-ab20-5d1b430c3e64` found entries at
07:16:57.903635, 07:19:12.351991, 07:22:26.027768, and 07:27:05.821657 Eastern.
At the 07:22 entry, price was 9.0497 and the current HOD was 9.53. Waiting-for-break decisions
occurred repeatedly during the preceding 7.28-to-8.50 rise. The journal did not
retain the rejected crossing state, so the exact number recoverable by each
correction is unknown. Spread failures above 200 bps are genuine vetoes and
remain unchanged. The strict R4-only / below-R3 policy inherently excludes
entry into later portions of a move; this correction does not relax it.
