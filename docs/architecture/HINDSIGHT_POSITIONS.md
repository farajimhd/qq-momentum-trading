# Full-session hindsight positions

Research-only oracle, `long-one-share-quote-dp-v1`. The chart's **Hindsight**
toolbar button generates idealized long positions for the selected date's entire
04:00–20:00 America/New_York session. It deliberately uses future information.
It is never supplied to the strategy, Portfolio, OMS, or causal indicator inputs.
The chart draws only positions intersecting loaded chart times; its full-session
count and profit can include times outside a shorter Backtest review interval.

## Objective and assumptions

Maximize additive net dollars for one share, allowing one open long position,
unlimited sequential round trips, and zero trades when none is profitable.
Entries use observed asks, exits observed bids. Default additional cost is 5 bps
of each side's price. No minimum holding time, cooldown, stop, holding limit,
liquidity participation, execution latency, queue, impact, or capital compounding
is modeled. It is an idealized benchmark, not executable profit or a forecast.
Strictly increasing action timestamps disallow a same-timestamp round trip or
exit/reentry. Equal objective values retain the existing path.

The linear-time dynamic program maintains the best flat cash value and best
holding value. At each timestamp it evaluates selling the previously held share
and buying from the previous flat state. Immutable backpointers reconstruct the
best completed trade sequence. An unclosed final holding is discarded. Quotes
with non-finite/non-positive prices, crossed markets, or non-positive sizes are
counted and rejected; locked positive-size quotes are allowed. Unordered input
fails. A 1e-10 dollar tolerance stabilizes equal-value comparisons.

## Authority and operation

`QmdHistoricalEventSource` reads quote-only pages with pinned revision and complete
coverage from QMD History's canonical archive authority. Source revision, rejected
quote count, elapsed time, and model assumptions accompany every result. No raw
flatfiles, new operational tables, or indicator warm-up are involved.

`POST /api/research/hindsight` accepts `ticker`, `session_date`, optional `cost_bps`.
`GET /api/research/hindsight/{id}` reports queued/running/completed/failed and progress.
One worker, at most three active/queued requests, eight retained jobs, and a
10-million quote / 50,000-position / 10-minute budget bound work. Repeated active requests coalesce;
new requests revalidate source revisions. Source errors and budget overruns never
publish partial optima. Results are ephemeral; a backend restart requires regeneration.

The dedicated paint-only chart primitive cannot affect autoscale, navigation,
strategy annotations, or execution. Arrows distinguish buys/sells and dashed
connectors pair positions. Dense labels are suppressed, but their markers remain.
The toolbar is available on historical intraday Charts & Quotes views only.

## Validation

Exhaustive enumeration checks the optimizer on 300 deterministic short quote
paths with three cost settings and duplicate timestamps. Other tests cover loss-only
sessions, costs, empty/unclosed positions, invalid quotes, ordering, complete-session
source integration, and coverage failures. The browser harness option
`--hindsight-positions` exercises the real API, captures the layer, and checks that
keyboard toggling restores the underlying chart image.
