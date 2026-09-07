# Full-session hindsight positions

Research-only benchmark `liquid-macd-interval-swings-v1`, covering the selected
04:00–20:00 America/New_York session. It deliberately uses future information
and never supplies strategy, Portfolio, OMS, or causal indicator inputs.
Counts cover the full session even if the chart displays a shorter interval.

## Selection

Find uninterrupted completed-1s MACD > signal intervals, including negative MACD.
Closed-bar state begins at bar_end and persists through seconds without trades.
Only a completed non-bullish or explicitly invalid bar closes it; session end
caps the final interval. A missing bar does not imply a MACD crossing. Buy the lowest eligible observed ask
within two seconds before opening, including opening. Restrict that lookback
to timestamps strictly after the preceding selected position's exit. Sell at
the highest eligible observed bid during the open interval, strictly after the
buy and before interval close. Equal prices select the earliest candidate.

Reject intervals with no eligible entry, no eligible exit, or non-positive net
profit. At most one position per interval; no short-gap or post-hoc merging.
This replaces the endpoint's earlier DP-plus-merging pipeline. The old optimizer
remains a tested research helper. MACD defines the search boundary, not sell time.

## Liquidity and costs

Default gates: finite positive non-crossed NBBO, spread <=100 bps of midpoint,
>=100 displayed shares on each side, and >=3 eligible trades totaling >=100
shares in the preceding one second. Trade activity uses the window
(quote_time - window, quote_time], in canonical source order. Invalid or
price-ineligible trades do not count; same-timestamp trades count only after
encountered. Future trades never supply liquidity evidence.

Only observed quote updates are candidates; no quote is carried forward through
quiet periods. Canonical NBBO snapshots do not expose separate bid/ask ages,
so independent side freshness is not certified. Gates apply at entry/exit,
not continuously throughout a holding. These are configurable prototype filters.

Profit is per share, at observed ask/bid with 5 bps additional cost per side.
No latency, queue, impact, participation, compounding, or guaranteed execution is
modeled. These are hindsight trough/peak labels, not live signals.

## Authority and bounded operation

QmdHistoricalEventSource reads canonical trades and quotes together with pinned
source revision and complete coverage. Historical authority is market_sip_compact
through QMD History. MACD uses canonical bars-stage 1s projections in bounded
hourly requests with four bounded readers, chronological consumption, same-session
seeding and complete retained provenance. Research selects required canonical
row fields without constructing full execution event objects. Both projections
share revision, completeness and pagination checks.
No private EMA approximation, flatfile read, or new operational table is used.

POST /api/research/hindsight accepts ticker, session_date, cost_bps (5),
max_spread_bps (100), lookback_seconds (2), min_displayed_shares (100),
activity_window_seconds (1), min_trade_count (3), min_trade_volume (100).
GET /api/research/hindsight/{id} reports state and progress. All parameters
participate in active-job deduplication and accompany results.

One worker, three active/queued requests, eight retained results, ten million
events, one million trades per rolling window, and ten minutes bound the job.
Accepted quote columns consume 24 bytes/event; raw events are not retained.
One completed candidate set of at most two million quotes (48 MB of columns)
can be cached. Reuse requires an exact canonical source-revision probe and
matching liquidity settings; changing lookback can reuse candidates. MACD pages
are freshly requested. Source changes force rereading; source errors fail closed.
Phase timings and cache use are returned with each result.
Failures never publish partial results. Results are ephemeral. Quote and
interval rejection counts explain exclusions.

## Chart and filter

The popover reuses chart settings sections and sliders with displayed values,
Apply and regenerate, interval,
valid-position and shown counts, net profit, and rejection details. Draft settings
apply on regeneration. The paint-only overlay does not affect chart autoscale
or execution. Position numbers identify MACD intervals and remain stable.

Hide small profits defaults on: retain net return >=max(500 bps, inclusive 25th
percentile of positive candidates). Fewer than four candidates use only 500 bps.
Returns divide net profit by entry cost including fees: 5% is approximately $0.15
at $3. The checkbox reveals all positive candidates. This is descriptive
filtering, not a significance test or a global profit optimum.

## Validation

Focused tests cover lookback clipping, sequential positions, in-interval peaks,
wide/undersized/stale-activity quotes, trade count/volume, invalid trades, costs,
source completeness, filtering, negative MACD, no-trade gaps, and cache invalidation.
Real-API browser review checks the popover, filtering, keyboard dismissal,
and preservation of the chart viewport when toggling the overlay.
