# V6 tradable-universe campaign

Run `scripts/build_swing_book_campaign.py` from the repository on the execution
host. It builds V6 directly from canonical seconds, with selected daily survivors
as the only carry. It does not create a V4 candidate history or a V5 conversion.

Run on the workstation using its configured Python environment and synchronized
checkout under `\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes` (the source root
specified in `AGENTS.md`). Set the working directory to that checkout, containing
`scripts/build_swing_book_campaign.py`. Source synchronization is separate from
campaign execution and follows laptop validation, commit and push.
The default secret file is
`\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env`.
Both the controller and ticker workers accept the workstation runtime UNC root
below. They also accept the documented laptop runtime root for explicit local
validation. An unavailable requested root fails; there is no fallback.
Using a workstation UNC destination does not move execution to the workstation:
the Python command must run on that machine to use its CPUs and memory.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$campaign='\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\structure-validation\v6-tradable-20250101-20260904'
python -B scripts/build_swing_book_campaign.py plan --runtime $campaign --start 2025-01-01 --end 2026-09-04 --workers 4 --threads 2
python -B scripts/build_swing_book_campaign.py run --runtime $campaign
```

`plan` freezes the latest published `q_live.feature_tradable_universe_v1`
membership with `is_tradable=1`, retains identity and membership provenance, and
checks canonical coverage. This is the **current tradable universe**, not a
historical survivorship-free research universe. Ambiguous identities, differing
market symbols and missing canonical history are explicitly deferred with reasons.
No source flatfiles are used. Storage must pass `live_market_ssd` checks.

Four worker processes each process one ticker in chronological session order.
Each query uses at most two ClickHouse threads by default. There is
one aggregation query at a time per ticker worker: the eight session chunks
are read sequentially, without a nested query pool. Thus 64 workers with two
threads each request at most 128 query execution threads, excluding ClickHouse
background work and other applications. This is a query budget, not a limit on
all server threads. Scheduling starts
larger event histories first. Workers have isolated logs and checkpoints;
failures do not strand other queued tickers. Configure concurrency at planning
time; the plan pins source-code hashes and settings. Changed code needs a new plan.
The launcher supports up to 64 workers and a combined query-thread budget of
128 (`workers * threads`). On the 128-core workstation, explicitly select
`--workers 64 --threads 2` when planning; the conservative default remains four
workers. This is a supported limit, not a measured 64-worker speedup.

Progress prints every second by default: active/queued/completed/deferred/failed/
interrupted counts, each active ticker's completed sessions and elapsed time,
and a measured ETA. ETA is unavailable until measurements exist and is approximate:
liquid tickers can cost much more than SUGP/JUNS. `--progress-seconds` changes the
report interval. `--tickers SUGP AAPL` on `plan` creates an explicit bounded pilot.
Pass `--progress-seconds` to `run` to override the display interval. The display
refresh does not rewrite the full manifest; durable state is saved on worker
transitions and controller shutdown. Session counters advance when each session
finishes, while elapsed times refresh every second.

```powershell
python -B scripts/build_swing_book_campaign.py status --runtime $campaign
python -B scripts/build_swing_book_campaign.py stop --runtime $campaign
python -B scripts/build_swing_book_campaign.py run --runtime $campaign
python -B scripts/build_swing_book_campaign.py run --runtime $campaign --retry-failed
```

Stop requests wait for the current session to finish and checkpoint; no forced
process termination is used. Resume verifies completed daily state and continues
unfinished tickers. Completed tickers are skipped. OS locks prevent duplicate
controllers and writers; if orphan workers still hold locks, wait for them to
finish or request stop before resuming. Exit codes: 0 completed, 1 failure,
2 deferred tickers remain, 130 stopped.

The campaign directory contains `manifest.json`, planning profiles and
`workers/<ticker>/worker.log` plus `progress.json`. V6 reports are in the adjacent
`<campaign-name>-v6/<ticker>` directory, discoverable by the existing book registry.
The manifest records per-ticker elapsed seconds, database, status and failure
reason. No full-universe runtime estimate is certified from the two-ticker test.
