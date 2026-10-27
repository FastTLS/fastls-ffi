# Continuous-transfer CPU protocol v2

This is a new Windows measurement workload for the selected ten G1 H1/H2
scenarios. It does not relabel, delete or repair the v1 150-run experiment.
The old ineligible controls and H1 gzip CPU failure remain historical results.

Before measuring, freeze the harness commit, exact SDK/native pairs, package
lock, unchanged v1 acceptance policy and independent fixture hash. Freeze all
150 scheduled runs: control-a and control-b use the same installed official
Python 0.1.4 bytes, candidate uses the reviewed S4 build. Run five complete
interleaved rounds, each in a fresh process. Do not stop when a ratio passes,
omit failed rounds, rerun individual measurements or overlap local build,
stress and CPU workloads. Preserve worker files and every resource sample.

Keep the 64 MiB response/upload payload, hash and protocol checks, connection
reuse, 16 MiB/s slow consumption and 32-way small-response scenarios. Bulk
workers repeat complete transfers for 16 seconds; slow-consumption workers
repeat for 64 seconds. Small-response workers retain 30 seconds and 10 seconds
of warmup. Bulk warmup remains 0.05 seconds. The final full transfer may finish
after the window; report that actual duration and all successful business
bytes. Preserve the external recursive process-tree CPU-seconds sampler;
cycles are not substituted for CPU time. Legacy runs without the explicit
continuous flag still perform their original single bulk transfer.

For each arm, pool measured CPU seconds and verified business bytes across all
five rounds. Require at least one observed CPU second per arm/scenario, so
near-zero quantized measurements cannot accidentally qualify. Reject shortened
windows, missing rounds, failed warmup/TLS/hash/protocol/handle checks, changed
inputs and missing metrics. Apply the unchanged acceptance thresholds in both
directions to the two fresh controls before evaluating the candidate:
throughput >= 0.85, eligible p95 <= 1.20, CPU seconds/byte <= 1.25, and peak RSS
increase <= max(16 MiB, 20% of control). Slow consumption still has no throughput
ranking. p95 requires at least 20 complete responses in every round.

The new controls establish an independent continuous-workload baseline. The
single-transfer historical absolute values are not comparable and are retained
as provenance, rather than silently reused as calibration. Failed A/A or
insufficient CPU invalidates the corresponding candidate verdict. A successful
v2 result applies only to this new workload and host; it cannot certify v1,
H3, browser identity, other platforms or a complete release by itself.
