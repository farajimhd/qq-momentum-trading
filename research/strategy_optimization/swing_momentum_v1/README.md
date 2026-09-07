# Swing v4 momentum discovery candidate

This long-only research policy uses the causal v4 swing book with p_norm >= 0.20.
It contains no ticker, session-date, or hindsight-label trading conditions.
Hindsight labels are evaluation targets only. Two already-inspected sessions
are discovery data, not a holdout or evidence of future profitability.

Entry uses completed one-second MACD > signal (either sign), a green bar,
three-second price progress greater than one current spread, no usable selling
imbalance below -0.3, and at least one initial-risk unit of room to the next
qualified resistance lower bound. Price inside a resistance band must first
clear its upper bound. Initial risk cannot exceed 300 bps. These are explicit
configurable policy parameters, not optimized per-ticker constants.

The initial stop is below the closer protective reference: the last completed
red candle close or a qualified support lower bound. During a position, only
a newly confirmed qualified support can raise protection. A resistance test
entered from below and subsequently rejected below its lower bound exits;
a completed MACD closure also exits. Existing execution-quality, pending-exit,
Portfolio and OMS safeguards remain authoritative. Reentry uses the same gates.

`evaluate.py` compares a MACD/pressure baseline, room filtering, and one/two
spread progress thresholds. It reuses immutable causal feature extracts and
reconstructs structure from the selected v4 build. Cache identity includes
the feature hash and book fingerprint. All output belongs under runtimes.

The execution proxy buys at the next fresh quoted-second ask and sells at its
bid, with 5 bps additional slippage each side; entry signals expire after one
second. It does not simulate depth, partial fills, Portfolio sizing, latency
within a second, or market impact. Net dollars are summed per one-share trade,
not account returns. Labels are consulted only after the replay completes.
Strong-label participation measures interval overlap, not correct entry timing.

Example (load the configured ClickHouse environment first):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python research/strategy_optimization/swing_momentum_v1/evaluate.py --features-root D:/TradingML/runtimes/quant-research-workbench/research/swing-evidence-20260907 --output D:/TradingML/runtimes/quant-research-workbench/research/swing-momentum-v4-20260907 --book SUGP=structure_book_1f0b39d4737a --book JUNS=structure_book_39c0307d368e
```

Discovery outcome: none of the compared policies was profitable after modeled
costs on either full session. Use this candidate for inspection, not promotion.
