# Swing evidence test candidate

Contract `macd-open-selling-veto-v1` is opt-in for the long momentum executor.
Earlier candidates retain their behavior. This candidate is for historical
testing; it is not an approved live release or a claim of improved returns.

The August 21 JUNS/SUGP discovery comparison found a narrow selling-imbalance
entry veto, substantial lost opportunities from positive-MACD/VWAP/structural
proximity gates, and no reliable standalone early-top exit. The runtime evidence
is under `D:/TradingML/runtimes/quant-research-workbench/research/swing-evidence-20260907`.

## Decisions

- Initial entry and reentry evaluate completed one-second MACD: line > signal,
  including when both are negative. There is no minimum gap, positive-line,
  green-candle, VWAP, R3, local-breakout or resistance-proximity requirement.
  The condition is an open state, not a fresh crossover requirement; if another
  gate prevents entry, a later completed bullish bar may enter. That later-entry
  behavior was not validated by the opening-only discovery analysis.
- Veto when the shared pressure evaluation is usable and its one-second
  classified trade imbalance is strictly below -0.3. Usability retains the
  existing freshness, minimum three trades, classified fraction >= 0.5 and
  quote-flow coverage requirements. Missing, stale or future pressure is neutral,
  not an exit or a recovery latch. This does not relax quote execution checks.
- Protective stop is one tick below the latest completed red candle's close,
  with the existing tick rounding and same-session validation. The red candle
  that just closed may supply the stop; a forming/future candle may not. Missing
  or nonprotective stop evidence blocks entry. The stop stays fixed after entry.
- Exit on completed 1s line <= signal, including equality and either sign.
  This is an explicit baseline, not a discovered optimal-top predictor.
  Protective stop, session flatten, operator exit and order-safety handling remain.
- No pressure, histogram-decline, local-retest, VWAP-loss or structural-retest
  early exit. No fixed target or structural trailing stop is submitted.
- Pending exits still block new entries. Current liquidity/spread requirements,
  original early-pop discovery, Portfolio allocation, OMS fill handling and
  historical session restrictions remain in place. Structural books remain
  available for inspection but do not gate this candidate's entries.

## Wiring and validation

`swing_evidence.apply` resolves the complete opt-in policy after historical
revision defaults so stale inherited experiments cannot silently re-enable gates.
The backend candidate builder clones the tested profile and historical run plan,
preserves approved profiles, and clones the plan's account mandates/deployment.
Creation uses `create_test_candidate` and the normal validated release pipeline.
It does not approve or change the live configuration.

Engine tests cover negative MACD, missing/stale/future pressure, the strict veto
boundary, completed versus forming candles, red-candle protection, MACD closure,
protective-stop precedence, reentry and pending capital/exit handling. Older
pressure, local-swing and momentum contracts are regression-tested separately.
Full-session trading performance must be assessed in a new backtest.
