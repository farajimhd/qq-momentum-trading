# Swing book v5

V5 applies the approved deterministic resistance selector to the existing v4
candidate detector. Supports retain their candidate geometry. It changes the
published book, not canonical trades or v4 evidence.

## Selection and causality

- Only active major candidates available at the current timestamp participate.
- Resistance areas have at most 100 bps total width, except a single candidate
  whose own band is wider. Adjacent gaps cannot chain into an unbounded area.
- The strongest member supplies the price and evidence grade; correlated member
  counts are not summed. Departures, independent/current-role retests and accepted
  crossings determine the grade. A newly flipped resistance cannot inherit a
  support's unvalidated strength.
- Resistance publication requires grade 30/100. This is not a probability.
- No `p_norm` or prior-close price-range cutoff applies to v5. Chart score filters
  can further hide resistance areas without changing strategy inputs.
- Streaming consumes completed canonical one-second bars. A missing trade second
  does not erase a level. New geometry and grade changes appear only when known;
  retrospective pivots do not backdate availability.

## Historical construction

`scripts/build_swing_book_v5.py` reuses a validated v4 candidate book. Its
ClickHouse query performs evidence scoring, bounded grouping, selection, and
interval compaction server-side. It does not fetch raw market events or repeat
the original candidate calculation. V4 must first cover every certified source
day in the requested range; otherwise the builder stops with a coverage error.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python scripts/build_swing_book_v5.py --tickers SUGP JUNS --start 2025-01-01 --end 2026-09-08 --threads 4 --runtime D:\TradingML\runtimes\structure-validation\swing-v5-20260908
```

Two ticker workers are supported, each using bounded ClickHouse threads and
memory. Reports record source identity, coverage, SQL timings and validation.
The builder's elapsed time measures selection from existing candidates, not
the original construction of those candidates.

The public `book` table stores ticker, stable area ID, price/bounds, role,
evidence grade, member IDs and half-open validity intervals. It uses
`ReplacingMergeTree`, a fixed ticker-hash partition and ordering by ticker,
level ID and valid-from. Readers require `FINAL`. All tables and parts must use
`live_market_ssd`; the builder and live writer check policy and placement.

`latest_state` holds one logical compact candidate checkpoint per ticker,
including sequence and hash. No candle history or visual segments are stored in
that state. Existing v4 closing states remain the immutable historical seed
authority for past replay sessions; do not delete them until migrated separately.

## Live and replay

`StreamingSwingBookV5` shares the v4 detector and caches the selected projection.
The replay cursor loads the preceding candidate close and advances the same
kernel with the canonical historical one-second source policy.

An explicitly configured live assignment can set `swing_book_v5` to a validated
v5 book ID. The live adapter bootstraps from certified candidate state and QMD
Live completed bars, then reads the recent completed-bar buffer at most once per
second. It excludes forming/future bars, validates splits and seed hashes, and
fails if intervening sessions need repair. The supervisor persists the compact
close after 20:00 New York time; transient intraday versions are not persisted.
The closing writer updates changed public intervals and verifies state readback.
No live assignment is enabled by building a book or choosing it in Backtest.

The live path still depends on QMD Live's canonical bar coverage. A gateway
outage or lost continuation requires repair, never a flatfile fallback.

## Chart validation

Choose **Swing book v5** for the ticker in the Backtest **Level book** selector.
The chart uses the existing Swing level book indicator with a resistance evidence
score slider (30–100), separate line/band opacity, labels and role visibility.
The separate Selected resistance toolbar overlay is retired. Older backtests
retain their pinned book; run a new test to inspect v5.

`scripts/validate_swing_book_v5.py` compares every historical closing/split boundary
against the Python selector and profiles real per-second continuation. Focused
tests cover cache invalidation, splits, future/forming-bar exclusion, strategy
band preservation, and closing persistence timing. Full live-session operation
still needs an observed gateway session; offline and mocked checks do not imply
live trading approval.
