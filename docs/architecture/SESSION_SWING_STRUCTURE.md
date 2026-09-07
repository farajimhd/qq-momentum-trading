# Session swing structure preview

`causal-session-swing-v4` is an opt-in chart prototype. It neither replaces the
existing historical book nor changes strategy decisions. No historical campaign
or persisted book is created. Click **Swing structure** in a historical chart;
the adjacent settings button exposes scale/reversal and opacity sliders.

## Causal contract

The shared Python kernel consumes ordered completed one-second OHLC bars. It
tracks directional-change extrema independently at local and major scales.
Let `floor = max(2 ticks, extreme price * 50 bps)`. Local reversal distance is
`max(floor, min(2 * prior volatility, 2 * floor))`.
Volatility is median true range of the previous 30 observed bars. Major distance
is three times local distance. These are provisional configurable defaults,
not thresholds fitted to JUNS or SUGP.

Reversal distance freezes at the candidate extreme, using prior-only robust
volatility capped at twice the floor (configurable). The cap prevents opening
spikes from locking confirmation behind an excessive distance. Falling volatility
cannot lower an existing candidate's threshold and cause a confirmation.
A higher high or lower low
replaces the candidate. Confirmation requires a subsequent bar closing beyond
the reversal distance AND moving away from the extreme versus the previous close.
A flat or recovering close cannot confirm a reversal. A bar cannot confirm its own extreme, because OHLC does
not provide high/low ordering. `pivot_at` is the extreme bar's end, not an exact
trade timestamp; `confirmed_at` and `valid_from` are the causal availability.
The chart starts lines at confirmation, never at the earlier pivot.

Levels use the observed extreme as price, with half-width `max(tick, 2 bps)`.
A nearby same-role, same-scale active pivot reinforces the anchored level;
it does not average prices or union connected bands. Opposite roles and scales
remain separate. Major swings are displayed by default; local swings are optional.

## Failed boundary tests

A second, symmetric detector identifies ceilings/floors tested without a large
reversal. It keeps one pending candidate per side and a 30-second approach window.
A candidate must be near that window's outer high/low and must have approached
from the inside by at least the major-scale bps/tick floor. This prevents quiet
flat trading or arbitrary internal prices from creating a new structural level.

Adjacent touching bars are one encounter and cannot confirm a level by count
alone. After at least three touching bars, a price move away by the major-scale
bps/tick floor can confirm a meaningful rejection. Alternatively, an entire
bar must move away by the local bps/tick floor (at least three ticks), followed
by a return and a failed close at least one contact tolerance inside the anchor.
That second independent failed encounter confirms a retest level. Candidates
expire after 120 seconds. Tests must stay within `max(3 ticks, 10 bps)` of the fixed
anchor; a close beyond it by more than one tick invalidates the candidate.
A new extreme beyond the tolerance starts a new candidate instead of widening
the old one. The normal narrow band remains anchored at the first boundary test.
Confirmed boundary levels are major and share the existing role/expiry lifecycle.
Same-role/same-scale matches reinforce the existing level, not a parallel copy.

The visual segment records `confirmation_kind` and frozen `reversal_distance`
(null for boundary-test confirmation), so diagnostics distinguish the mechanisms.
This detector uses no future bars or MACD labels. Its 30-second deque and two
pending candidates keep its memory and per-bar work bounded.

Before founding another major level, the algorithm checks for an established
active major support/resistance pair enclosing the recent trading range. Both
boundaries must predate the candidate and be at least 15 seconds old. At least
five observations spanning ten seconds in the last 15 seconds must remain
enclosed, including the current close. Interior candidates are counted as
`suppressed_interior_major`; local swings remain available. A breakout or a
pending/invalidated boundary releases this suppression. No proximity merging
or chart-only hiding is used to achieve the sparse major book.

An active level breaks after two completed closes beyond its far bound plus
one tick. Its original role remains pending. A later bar must touch the band,
then a subsequent bar must close on the new side to confirm a role reversal.
A close back on the original side restores the original active role. Distinct
contact/rejection encounters increment strength; consecutive touching bars do
not repeatedly increment it. These encounters are internal diagnostic state.
The visual API does not export a trading score or one segment per score update.

Levels expire after 30 minutes (local) or two hours (major) without a test.
Expiry and visible role/state changes close the old validity segment; earlier segments
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
Rejections without a visual change emit no segment. Both awaiting-retest and
retest-contact are rendered as `pending`; the exact internal lifecycle still
advances. Event counts expose these omitted visual no-ops. Accepted breaks,
failed breaks, role reversals, creation and expiry remain visible and causal.
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

V2 diagnosis on 2026-08-21: JUNS V1 emitted 27,998 segments, including 11,304
score-only rejection updates and 5,180 visually identical retest-contact updates.
The default 20,000 guard correctly rejected it. V2 retains that guard and produced
13,961 visual segments in a memory-only preview. SUGP produced 5,993 segments;
its $3.20 major support confirmed at 04:00:48 ET instead of 04:10:34. These are
diagnostic session checks, not a historical campaign or trading-quality acceptance.

V3 replaces V2's adaptive threshold after the JUNS $6.32 case showed that a
shrinking threshold could confirm while price recovered. With the robust cap
and a fixed distance of $0.1896, that resistance confirms on the actual decline
at 07:09:39 ET. JUNS tests at 07:13:56, :57 and :58 confirm the $6.88 ceiling
at 07:13:58 through the boundary-test detector. Full memory-only session previews
returned 19,629 JUNS / 8,309 SUGP visual segments, within the unchanged limit.

V4 corrects V3's promotion of continuous touching bars as independent tests.
On the default SUGP 2026-08-21 preview, major levels at 04:30 ET fell from 51
to 11 in the initial comparison. The JUNS $6.88 level at 07:13:58 remains
eligible because the repeated encounter ends in a substantial rejection,
not because three adjacent candles automatically make three independent tests.
