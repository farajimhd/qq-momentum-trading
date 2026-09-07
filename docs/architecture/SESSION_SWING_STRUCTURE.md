# Session swing structure preview

`causal-session-swing-v1` is an opt-in chart prototype. It neither replaces the
existing historical book nor changes strategy decisions. No historical campaign
or persisted book is created. Click **Swing structure** in a historical chart;
the adjacent settings button exposes scale/reversal and opacity sliders.

## Causal contract

The shared Python kernel consumes ordered completed one-second OHLC bars. It
tracks directional-change extrema independently at local and major scales.
Local reversal distance is `max(2 ticks, price * 50 bps, 2 * prior volatility)`.
Volatility is mean true range of the previous 30 observed bars. Major distance
is three times local distance. These are provisional configurable defaults,
not thresholds fitted to JUNS or SUGP.

Each extreme freezes its own reversal distance. A higher high or lower low
replaces the candidate. Confirmation requires a subsequent bar closing beyond
the reversal distance. A bar cannot confirm its own extreme, because OHLC does
not provide high/low ordering. `pivot_at` is the extreme bar's end, not an exact
trade timestamp; `confirmed_at` and `valid_from` are the causal availability.
The chart starts lines at confirmation, never at the earlier pivot.

Levels use the observed extreme as price, with half-width `max(tick, 2 bps)`.
A nearby same-role, same-scale active pivot reinforces the anchored level;
it does not average prices or union connected bands. Opposite roles and scales
remain separate. Major swings are displayed by default; local swings are optional.

An active level breaks after two completed closes beyond its far bound plus
one tick. Its original role remains pending. A later bar must touch the band,
then a subsequent bar must close on the new side to confirm a role reversal.
A close back on the original side restores the original active role. Distinct
contact/rejection encounters increment strength; consecutive touching bars do
not repeatedly increment it. The optional bounded score `1-exp(-strength/3)`
is an encounter-strength measure, not a calibrated probability or the old
book's min/max prominence. No strategy uses this score.

Levels expire after 30 minutes (local) or two hours (major) without a test.
Expiry and role/state changes close the old validity segment; earlier segments
remain unchanged for as-of display. The session starts empty: no historical
anchors or cross-session/split carry are implemented in this preview.

## Runtime and limits

`POST /api/research/swing-structure` reads canonical QMD History bars in four
independent four-hour windows with at most four readers. It requests no existing
structural computation or MACD warmup. Source completeness, bounds, order and
the full-session source revision before/after the read are checked. There is
no raw SIP fallback. Requests require a completed 04:00–20:00 New York session.

The detector is O(bars); lifecycle work is O(bars * active levels), with expiry
and an explicit 2,048-active-level ceiling. Output contains changes only, with
a 20,000-segment ceiling. Both limits fail explicitly, never silently truncate.
Only one preview request is admitted at a time; other requests receive 429.
Results are kept in the requesting chart's memory, not in ClickHouse or files.
The renderer contributes nothing to autoscale, clips at the chart's available
time, and never feeds strategy state.

The ordered Python stage is intentionally small; ClickHouse/QMD supplies the
already aggregated bars. This is not a claim that the whole algorithm executes
inside ClickHouse. The popover reports end-to-end and kernel timing separately.

## Validation

`python -m unittest tests.test_swing_structure` covers causal prefixes, OHLC
ambiguity, small oscillations, scale separation, breakout/retest role flips,
expiry, invalid input, resource bounds, and the FastAPI/QMD contract with fixtures.
Set `PYTHONDONTWRITEBYTECODE=1` before repository Python commands.

The managed frontend review's `--swing-structure-fixture` option validates
sliders, keyboard toggling, visible segment painting and unchanged chart rendering
after hiding the overlay. It intercepts the preview API with synthetic segments
and does not generate real market levels. Historical trading quality remains
subject to user visual validation before any book construction or strategy use.
