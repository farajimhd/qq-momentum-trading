# MACD price-swing labels

Research-only algorithm `price-macd-bidirectional-swings-v1` labels the selected
04:00–20:00 America/New_York session using canonical trade prices and completed
1s MACD. It deliberately uses future prices and never supplies live strategies,
Portfolio, OMS, or causal indicator inputs. Full-session counts may include
labels outside the currently visible chart window.

## Symmetric label rules

MACD > signal opens a long interval; MACD < signal opens a short interval.
The sign relative to zero is irrelevant. Equality or an explicitly invalid
indicator closes the active interval; a crossover closes one and opens the
other. Completed state starts at bar_end and persists through seconds without
trades. Session end caps the final interval.

For each interval, independently:

- Long: select the lowest trade price in the opening lookback, then the highest
  trade price during the open interval, strictly after entry.
- Short: select the highest trade price in the opening lookback, then the lowest
  trade price during the open interval, strictly after entry.

The lookback defaults to two seconds and includes the opening timestamp. Exit
search excludes the interval's closing timestamp. Equal extrema use the earliest
observed candidate. An interval without an entry, exit, or favorable directional
move is counted as skipped. No portfolio sequencing rule clips another label's
lookback: independent labels can overlap or share a turning point.

Every label records direction, entry/exit price and time, low/high swing roles,
MACD boundaries, gross_move_per_share, gross_return_bps, and label_available_at
(the interval close). Direction-adjusted return divides the favorable price
change by entry price. These are gross price moves, not executable P&L.

## Data and execution separation

Only finite, positive, canonically price-eligible trade prints define extrema.
Bad-print rejection is data-quality validation. Trade size, activity, spread,
NBBO depth, fees, borrowing and other execution constraints do not gate labels.
Quotes are not fetched. Both directions remain in the returned dataset so that
training and execution policy can independently choose long, short, or both.

All valid positive labels are retained. The optional chart filter hides gross
moves below 5% (500 bps); it does not remove them from training data. The previous
net-profit/lower-quartile filter does not apply to this label algorithm.

## Authority and performance

QmdHistoricalEventSource reads certified canonical trades through QMD History
with pinned revision, completeness and pagination checks. Research projects only
needed row fields; full execution-event hydration is unnecessary. MACD comes
from canonical bars-stage 1s projections in hourly pages, using four bounded
readers, chronological consumption, same-session seeding and retained provenance.
No raw SIP flatfiles, private MACD calculation or operational table is used.

One worker, three active/queued requests, eight retained results, ten million
trade prints and ten minutes bound work. Price/timestamp columns use 16 bytes
per accepted print. One candidate set up to three million prices (48 MB of
columns) may be cached. Every reuse rechecks the canonical source revision;
changed data or session forces rebuilding. Lookback changes reuse the same price
columns. MACD is freshly requested. Errors never publish partial labels.

POST /api/research/hindsight accepts ticker, session_date and lookback_seconds
(default 2, range 0–30). Removed liquidity/cost parameters are rejected rather
than silently used. GET /api/research/hindsight/{id} reports progress and result.
The result includes complete parameters, price basis, source/indicator provenance,
phase timings, cache use, direction counts and rejection counts. Jobs and cache
are ephemeral, so backend restarts require regeneration.

## Chart

The chart-settings-style popover contains a swing-lookback slider, Apply and
regenerate, Both/Long/Short selector, optional Hide small moves checkbox, and
counts. Direction and display filters update the overlay without regeneration.
Long entry/exit and Short entry/exit labels use distinct theme colors and
transaction-direction arrows. The paint-only overlay does not affect autoscale
or execution. Position IDs identify MACD intervals and remain stable on filtering.

## Validation

Tests cover mirrored extrema, chronological exits, shared turning points,
independence from liquidity fields, retaining small labels, invalid prints,
negative MACD, no-trade gaps, source completeness and cache invalidation.
Real-API browser review exercises the slider, regeneration, direction counts,
filter reversal, keyboard dismissal and unchanged chart viewport.
