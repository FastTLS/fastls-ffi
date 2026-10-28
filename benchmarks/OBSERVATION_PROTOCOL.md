# S4-R1 failure observations

This driver change preserves stderr bytes, stdout bytes, an incremental JSONL
journal, received/handled/acknowledged monotonic times, sampler call costs,
partial resource observations, exit codes and final sidecar results. Python
stack deadlines are opt-in for diagnostics. A timeout, malformed marker/result,
nonzero exit, incomplete boundaries or failed process cleanup remains invalid.
An existing run identifier cannot overwrite earlier evidence.

Observed descendants are identified by PID and creation time, then reaped on
failure. This includes the actual interpreter behind a Windows venv launcher.
The controlled benchmark worker does not daemonize. This is not a sandbox for
arbitrary processes that deliberately escape discovery.

Every progress message is retained. Each controller iteration drains up to
1024 queued messages before periodic sampling, acknowledges lifecycle boundaries,
and requests fixture state only for the latest progress in that batch while
measurement is active. Such a fixture snapshot is a current observation, not a
reconstruction for earlier queued progress. Sampling targets a minimum 0.05 s
gap after a sample completes; expensive process queries can make actual gaps
longer. Their costs and source/receipt/acknowledgement times are recorded.

The prior driver sampled both process trees after each queued progress message.
Workers emit slow-consumer progress about every 0.2 s. On the retained host,
successful historical H2 slow runs already have external stop observations
14.59–77.11 s later than the worker's measured interval. A finite progress-flood
subprocess test exercises the starvation mechanism without an SDK or network.
The failed historical run lost its markers; its exact stalled phase cannot be
reconstructed. Its failure remains unchanged.

After fault tests and H1/H2 SDK preflights, freeze a 14-run diagnostic plan:
five paired H2 upload rounds (64 MiB, 16 s), two paired H2 slow-consumer rounds
(64 MiB, 64 s, 16 MiB/s ceiling), interleaved in a predefined order. Both labels
use the same published 0.1.4 SDK and native library. Use new processes, retain
all failures, keep the existing warmup and `warmup + seconds + 90` wall budget,
and do not retry or stop early. Optional Python stack dump occurs 5 s before
that unchanged wall budget. Preserve before/after source and artifact hashes.

These are bounded diagnostics, not a third certification or replacement v2
rounds. They do not test a candidate performance ranking. Any observed upload
A/A p95 ratio is reported against the original 1.20 criterion without changing
the retained failed gate. Further certification requires a new complete plan.

Windows resource observation uses one documented Tool Help process snapshot per
tree sample for ancestry and thread counts. Every live descendant is included;
creation times exclude orphans whose original parent PID has been reused. CPU,
RSS and handles retain the same psutil counters and units. No process listing
is cached between samples. Enumeration and permission failures invalidate the
observation rather than producing zero resource values. Cleanup uses the same
tree discovery and retains its PID/creation-time checks before termination.

This avoids repeated whole-system queries from `children()` and `num_threads()`.
The Windows venv launcher, actual interpreter and nested children remain in
scope. Linux observation is unchanged. Timing guards, performance rules, worker
code and test windows remain unchanged. A sampler revision requires a separate
complete certification; it cannot replace failed rounds in an existing cohort.
