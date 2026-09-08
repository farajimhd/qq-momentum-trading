# V5 resistance breakout strategy

Opt-in contract `swing-v5-breakout-1`, profile `swing-v5-breakout`.
Use a V5 book when creating the backtest. Older candidate contracts are unchanged.
No ticker names, dates, hindsight outcomes or fitted thresholds enter the policy.

Entry requires bullish 1s MACD (line > signal, including below zero), price above
VWAP, and a fresh upward cross of the upper bound of one of the three highest
qualified resistances below the causal session high. The entire resistance band
must be above VWAP. Existing volume, liquidity and spread admission still apply.
The forming price must exceed the previous completed 1s body's maximum, and net
movement across at least 400 ms must be positive. Small intervening downticks are
allowed. Default maximum observation gap is 250 ms; a sparse tape cannot prove
continuous observation of the direction window. Break evidence expires after
one second or a return to/below the crossed boundary. A new level appearing
below price is not itself proof of a crossing.

The stop starts below the broken band's lower bound by 5 bps, with at least one
tick offset and rounding down. Each higher resistance crossing raises it; it
never falls. A newly confirmed qualified resistance within the observed move's
high also raises it. If price is already below that stop, the strategy exits
immediately rather than submitting a stop above price. Only confirmed, available
V5 rows are used, not unpublished provisional detector candidates.

The target is one tick below the lower bound of the second resistance above
`broken upper bound + mean completed candle absolute body`. The initial mean
is zero. At each higher break, the preceding move's mean selects the new target,
then the accumulator resets at that break. Exclude the partial candle containing
the break, and count each subsequent completed 1s candle once. Targets may advance
but never retreat; if two known resistances are unavailable, retain the existing
target. Initial entry requires two known target resistances and a target above
the executable ask. Stop and target replacements can be emitted together.

Target exits cover the full position; no partial runner, independent MACD exit,
support-based stop or red-candle stop applies. Normal session flattening, manual
exits, OMS protection and pending-exit guards remain. Reentry uses the same entry
conditions and a new crossing after the outstanding exit completes.

Validation: focused synthetic causal observations through the production strategy
engine and helper tests. No backtest was run for this change.
