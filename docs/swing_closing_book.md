# Swing closing-book validation

## V5 symmetric streaming selection

New V5 builds use `symmetric-level-evidence-selection-2`. Both supports and
resistances use bounded area grouping and the strongest member's evidence;
correlated candidates never have their scores summed. The default grade cutoff
is 30/100, with a 100-bps maximum area width. An individual oversized band is
rejected as well. Grades are evidence measures, not profit probabilities.

For an unchanged role, departure contributes up to 40 points, independent
retests up to 40, and current-role retests up to 20. Each accepted crossing
deducts 20. After a role reversal, the V4 lifetime departure maximum and old
independent retests cannot certify the new role: only current-role retests
contribute. This rule is identical for support and resistance.

During a session, qualified bands remain in raw streaming evidence while awaiting a retest,
with their original identity and qualification timestamps. They disappear on
removal, replacement by an overlapping qualified area, or a role change.
Broken supports are excluded from the strategy's active protection projection.
The role change must qualify anew. Completed seconds are applied once; polling
frequency cannot decide whether qualification was witnessed.

Selection invalidation uses major-level geometry, lifecycle and score operands.
Local-only changes, observation timestamps and already-saturated departure
measurements do not trigger another sort/group pass. Market I/O remains outside
the calculation. Live catch-up validates and deduplicates a full batch before
mutation, rejects conflicting completed candles, and recovers a lost recent-bar
buffer from paged canonical QMD bars. Session finalization uses the same gate,
excluding forming bars. History paging stops at the consumed cutoff.

Build reports pin the selection contract and selector hash. Existing V5 reports
without that field use `resistance-evidence-selection-1` and retain the original
unscored supports. New output gets a new isolated database; existing books and
saved run IDs are not rewritten. The builder validates SQL/Python parity for
both roles and verifies `live_market_ssd` policy and part placement.

The Backtest selector labels new builds **Swing book v5 · scored S/R**. Automatic
selection prefers them over legacy builds with the same coverage. Charts accept
role-prefixed hashed IDs and display evidence scores for both roles.

Historical builds run `scripts/build_swing_book_v5.py`. Its
`symmetric_selection_sql()` query scans the certified V4 candidate table once
and groups by ticker, closing timestamp **and role**, so nearby supports and
resistances cannot merge. Only geometry, identity and score enter the grouping
arrays; source JSON is not carried through the sort/fold. The legacy single-role
SQL entry point remains available for reproduction.

The builder writes `selection.sql`, query profiles, per-side interval counts,
the pinned selection contract and validation evidence under the specified
runtime directory. It uses the V4 checkpoints rather than replaying market
events. Existing V5 databases are preserved; query changes produce a new
fingerprinted output. For example, using the script's managed environment-file
default (override `--env-file` when required):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/build_swing_book_v5.py --tickers SUGP JUNS --start 2025-01-01 --end 2026-09-04 --threads 2 --runtime D:/TradingML/runtimes/structure-validation/swing-v5-bulk
```

Choose an end date covered by the certified V4 source. Missing certified source
days fail validation; the builder does not fall back to raw market files.

`causal-swing-closing-book-3` reuses the accepted `causal-session-swing-v4`
detector. It is an opt-in Backtest book, not a Live promotion or a replacement
for retained v18/legacy ClickHouse builds.

## Historical qualification in v3

The session detector still publishes its confirmed intraday swings. A new
major level carries into later sessions only when it survives the close and:

- Its observed close-price departure reaches the maximum of 3% of its price,
  six times prior 30-bar median true range, 15% of the session range, and three
  price ticks; or
- It has two independent retests, each separated by a whole bar outside the
  band and a material departure. The departure threshold is the maximum of
  1% of price, three times prior median true range, 5% of the range observed
  when the level was confirmed, and two ticks. The best departure must also
  reach 5% of the completed session range for closing qualification.

Birth thresholds use only prior observed bars. Completed-session range is used
only to select the next session's seed, never to rewrite the current session's
earlier output. Untouched qualified anchors do not expire from age. Two
accepted breaks without an intervening independent material retest retire an
anchor; a material retest resets that crossing counter. Retirement ends future
use, while prior persisted intervals remain available as of their own dates.
Split adjustment scales the added dollar-distance fields along with geometry.

These fixed prototype rules are not optimized against P&L or asserted to be
calibrated trading signals. V1/v2 behavior and source identities remain
available. Prominence and p_norm are unchanged. The chart legend now reports
active filtered levels instead of counting all historical drawing segments.

## Input and causality

ClickHouse aggregates canonical `market_sip_compact.events_YYYY` into OHLC
seconds. Four bounded queries per ticker may run concurrently, with four
ClickHouse threads per query by default; the controller runs at most two
tickers. Only aggregated seconds and metadata leave ClickHouse. The ordered
detector runs in Python, not inside a SQL array fold.

New builds use the recovered `historical-sip-condition-v1` authority: certified
archive events in SIP order with canonical last/high-low condition eligibility,
including the established extended-hours Form T handling. Execution-clock
sidecars are not required. The source policy and condition rules are bound to
the build fingerprint and replay uses that same policy. This excludes
condition-ineligible reports; it does not claim exact participant-time delay
filtering. Existing execution-clock-based builds retain their original policy
and validation. No flatfile fallback is allowed.

Each completed second becomes usable at its end. Replay advances only through
the requested cutoff. Loading a complete session's input does not make future
seconds available to the detector. The source revision is checked before and
after aggregation and against the revision used by the closing build.

## State and storage

Each session resets transient pivot/stall/volatility candidates, while carrying
the prior closing levels and unique-ID sequence. Major levels no longer expire
from elapsed time alone. Local levels retain their 1,800-second session-time
lifetime. Version 1 retains its original 7,200-second major lifetime so existing
backtests reproduce their pinned contract.

Confirmed breaks remove a major level from the active strategy/chart output.
If it survived an earlier close, its compact dormant state remains persisted
for a subsequent failed-break recovery or confirmed retest/role reversal.
New levels broken before their first close are not persisted. Repeated nearby
same-role pivots reinforce the existing anchor; overlap merging remains at load
time. Retention alone does not guarantee a sparse book: measure the active,
merged population and score distribution before adding a retirement rule.

Only major levels are published to the strategy/chart. Compact surviving local
levels are also stored because they affect subsequent detector state. There
are no persisted intraday segments, raw events, or evidence arrays.

The isolated database has three `ReplacingMergeTree` tables, all explicitly on
`live_market_ssd`, partitioned by `cityHash64(ticker)%32`:

- `book`: level geometry, role, prominence, compact lifecycle state and
  half-open validity intervals; ordered by `(ticker,valid_from_us,level_id)`.
- `sessions`: regular-session close, sequence, source revision and closing-state
  hash; ordered by `(ticker,session_date)`. Written only after verifying the book.
- `split_audit`: effective time, factor, affected-row count, before/after hashes
  and corporate-action source metadata.

At a split, old versions end at the effective session opening and adjusted
versions start then. Earlier prices and scores remain unchanged. The next
session continuation applies the same factor to the prior seed. Source
`inserted_at` is retained as audit provenance; economic adjustment uses the
reported effective date, not the later local import date. Real split-session
validation uses the source policy pinned to that build.

Daily writes are deterministic and resume from verified closing hashes. Source
or writer-code changes require a new runtime/build identity. Partial writes
have no session commit marker and are repeated on restart. Table policies and
actual active part placement are checked before and after the campaign.

## Score and presentation

Prominence is `log1p(strength)`, where strength starts at one and increments
on the detector's rejection encounters. It is an encounter-strength measure,
not a calibrated profit probability. Legacy raw-score cutoffs are not validated
for this new book. Same-role overlapping merges and frozen prior-session
min/max normalization remain at load time. An empty prior major book yields
`p_norm=null`; it does not invent a cross-sectional score.

For geometry validation, select **Backtest → Level book → Swing book v3** for the
ticker and set the structural indicator's **Minimum p_norm** to **0**. Zero
disables the display threshold, including for unscored levels. This does not
relax strategy gates. Do not enable the session-only Swing structure overlay
at the same time if comparing the persisted book in isolation.

## Run

Run from the repository through the installed Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python scripts/build_swing_structure_book.py --tickers SUGP JUNS --start 2025-01-01 --runtime D:/TradingML/runtimes/structure-validation/my-swing-build
```

The default range starts January 1, 2025 and ends at the latest certified
source session through today. Missing source or condition-rule certification
fails closed; missing execution-clock sidecars do not block this policy.
Old books remain retained. Repeating the identical command verifies and resumes
the existing build without increasing logical row counts.

## Indexed continuation

V2 continuation uses an interval index for bar contacts, ordered boundaries
for gap/break/retest transitions, and local-level expiry deadlines. Unaffected
dormant anchors are not revisited on every bar. Indexes are derived from the
closing state and are not persisted. The original transition rules, output
ordering, and checkpoint payload remain unchanged. Legacy v1 retains its
original scan. Writer fingerprints include the index implementation.

Historical v2 has an 8,192-level fail-closed memory budget, independently of
the session preview's 2,048-level budget. This accommodates retained dormant
anchors; it does not drop, rank, merge, or expire levels to fit the budget.

Historical SIP-condition eligibility is the deliberately approved archive
approximation, as restored by `d44d09a0`. Do not confuse it with the separate
execution-aware chart-bar contract or silently change either existing build's
policy. A change of source policy creates a new build identity.
