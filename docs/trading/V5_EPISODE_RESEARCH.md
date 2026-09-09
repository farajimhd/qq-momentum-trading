# V5 episode research contracts

Research candidates are immutable configuration revisions. A candidate's result
also depends on the source revision, canonical event/indicator authority, V5 book
fingerprint, execution simulation profile, initial capital and evaluation window.
Record all of them. Reusing a selected window makes it development data; hindsight
labels are never strategy observations or executable-profit claims.

## Optional episode management

`episode_management` opts a MACD episode candidate into explicit management
settings. Omitting it preserves the earlier episode policy.

| Setting | Default | Meaning |
| --- | ---: | --- |
| `rejection_from_below` | true | Arm a resistance attempt only when observed price approaches its frozen band from below. |
| `rejection_closes` | 1 | Consecutive completed 1-second closes below the rejection boundary. |
| `rejection_atr_multiple` | 0 | Subtract this multiple of the last completed ATR from the contacted lower band; freeze it at contact. |
| `stop_atr_multiple` | 0 | Keep a ratcheted stop at least this many completed-bar ATR units below a confirmed broken resistance's lower band. |
| `take_profit_fraction` | 1 | Fraction attached to the structural target. The remainder keeps its stop and cannot inherit the target. Zero makes the entire position a protected runner without a fixed profit order; structural entry eligibility is unchanged. Requires an explicit protection profile. |
| `entry_on_close` | false | Authorize entries only at a completed 1-second close, strictly above prior episode candle bodies plus the configured offset. |
| `entry_range_seconds` | 0 | Additionally clear the highest completed candle high in this observation window. Persists across MACD resets and is bounded to one hour. Close-only entry excludes the candle being evaluated; intrabar entry includes all completed candles. |
| `profit_trail_atr_multiple` | 0 | Optional completed-close peak minus current completed ATR stop. Zero disables this research departure from structural-only ratcheting. It never lowers existing protection. |
| `profit_trail_activation_atr` | 1 | Arm that trail after a held completed close exceeds actual average entry by this many ATR units frozen at entry approval. |
| `entry_confirmation_window_ms` | 0 | With close-only confirmation, permit execution during this bounded window after the close, strictly less than one candle interval old. Both the confirming close and current price must clear its frozen threshold; current VWAP, MACD and execution checks still apply. Maximum 1000 ms. |
| `maximum_macd_line_bps` | 0 | Optional ceiling on positive fast/slow MACD separation divided by its normalization close. Zero disables it. With completed MACD evaluation, both operands remain frozen at that close. |
| `entry_minimum_close_location` | 0 | Optional minimum `(close-low)/(high-low)` of the confirming candle. Requires close confirmation; flat or invalid candles fail closed. Zero disables it. |

The closing candle is compared with prior completed candles before being retained
in the episode maximum for the next decision. MACD evaluation remains separately
controlled by `macd_evaluation_mode`. Research using completed candles must select
`completed_1s`; a missing MACD value cannot manufacture an episode reset.

Touching a resistance is not a completed break. A close above its upper bound
clears the attempt. Rejection contacts and gap averages belong to the position
lifecycle. Volatility-dependent entry fails closed if completed ATR is unavailable.
Existing protective stops remain active when new evidence is unavailable.
Planned partial targets do not latch the lifecycle's liquidation cause. A later
stop or managed exit owns that cause and its applicable re-entry policy.

## Persistent acquisition reservation

An ask-following approved quantity needs price headroom in cash, exposure and
planned risk. Portfolio admission freezes `entry_funding_price` from the configured
acquisition buffer and sizes the quantity against that price. Repricing consumes
the same reserve and rechecks normal account and risk limits.

Entry commissions reduce cash plus funded position cost. The reservation therefore
tracks anticipated remaining fees separately. If risk capital is C, remaining
per-share risk is r, anticipated fee per share is c and the risk fraction is f,
the quantity ceiling is `C*f/(r+c*f)`. Other pending fee reservations reduce C;
filled fees already appear in actual cash. This avoids using fees twice while
preserving capacity for the approved remainder. Old persisted reservations default
the new fee field to zero and remain subject to normal reauthorization.

Unchanged denied reprices use the existing bounded retry interval. Changed prices
or remaining quantities can be reconsidered promptly. A capacity denial records
the limiting portfolio constraints; it never silently increases risk limits.

## Reproducible workflow

- `scripts/create_swing_gap_candidate.py` creates a new candidate through the
  normal configuration authority from an explicit parameter patch. Its optional
  `--mandate-risk-fraction` lowers the cloned mandates' planned risk limit without
  changing the account policy or allowing an increase over the source mandate.
- `scripts/run_strategy_experiment.py` runs that candidate through the real
  `ReplayRunController`, preserving baseline definitions unless explicit date,
  time or simulation overrides are supplied. It records source hashes and a
  restart ledger, prevents simultaneous use of the same output directory, and
  preserves prior failed/interrupted attempts. Changed source requires a new
  experiment directory. `--new-order-activation-delay-ms` delays eligibility of
  newly submitted orders, using the latest causal instrument/decision clock.
  It is persisted in the run definition and remains effective after recovery.
  This isolated sensitivity test does not model acknowledgement, cancellation
  or amendment transmission delays; those remain immediate. Zero preserves
  existing matching behavior. A delayed order fills only on a later eligible
  matching event or explicit current-quote match, never on a future quote.
- `scripts/audit_strategy_positions.py` joins every canonical lifecycle to its
  entry decision and Portfolio approval by identity, hydrates verified journal
  evidence, checks entry thresholds and approved quantities, and measures costs
  and closed-equity drawdown. Whole-candle excursions omit partial holding bars.
- `scripts/run_strategy_batch.py` executes a frozen JSON list of experiments
  sequentially, reusing preparation caches within one process. Each row contains
  a unique directory `name`, `candidate`, `plan`, `baseline_run` list, and optional
  experiment date/time, simulation-profile or new-order-delay overrides. Existing
  experiment ledgers remain authoritative for restart. Failed cases are recorded;
  continuing independent cases requires `--keep-going`. Run at most two batches
  concurrently for this bounded research workflow.
- `scripts/benchmark_strategy_moves.py` reads canonical eligible trades and
  produces offline MACD episode extrema and trade-price excursions. It does not
  bypass liquidity, spread or execution constraints in strategy evaluation.
- `scripts/benchmark_strategy_quotes.py` reads the same canonical quote authority
  for offline contemporaneous bid marks, recording invalid/crossed quote counts
  and zero displayed sizes. `measure_strategy_equity.py --mark-source bid` uses
  those marks to compare spread-sensitive inventory drawdown. This is top-of-book
  valuation, not a claim that all inventory can be liquidated at that bid.
- `scripts/render_strategy_audit.py` renders actual fills and effective protection
  paths against the eligible-price stream, including an offline large-move inset.
- `scripts/summarize_strategy_research.py` consolidates every experiment and
  audited position without selecting a winner or treating active cases as zero returns.
- `scripts/measure_strategy_move_capture.py` marks actual execution inventory
  across offline large-move intervals. Its marked interval P&L is not an
  executable liquidation return, and those hindsight extrema never enter strategy inputs.
- `scripts/measure_strategy_equity.py` measures last-eligible-trade equity,
  including unrealized drawdown and carried inventory in chronological slices.
  It requires matching benchmark coverage and reports stale boundary mark ages.
  Slices use `[start,end)`; the full run includes executions at its exact end.

Use each script's `--help` for required inputs. All generated evidence belongs
under the designated machine runtime root, never the source repository. Keep
`PYTHONDONTWRITEBYTECODE=1` for repository Python commands.

Compare policies on identical capital and execution assumptions. Report both
symbols separately, transaction costs, drawdown, acquisition completeness and
large-move participation. Test nearby parameter settings, execution stress and
previously uninspected sessions before calling a candidate stable. A high score
on the repeatedly inspected development cases does not establish robustness.
