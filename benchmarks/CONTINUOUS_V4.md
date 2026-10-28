# Independent extended-window CPU certification v4.1

This is a new complete experiment. It does not edit, replace or combine any
earlier v3 run. S6's first v3 cohort had two delayed observations and unstable
slow-H2 controls. Its separate sampler-repaired v3 cohort completed all 150
valid runs and all timing guards, but slow-H2 A/A CPU/byte was 1.2628336756,
above the unchanged 1.25 limit. Both cohorts remain ineligible.

Before any v4 candidate measurement, an independent calibration uses only
the same official 0.1.4 SDK/native control. Five fresh processes per label run
the identical slow-H2 workload at the proposed longer window. All ten runs,
their order, original CPU/RSS/p95 rules and v3 timing eligibility must pass.
Raw process cycles are diagnostic; they are never converted into CPU seconds
or substituted for the CPU/byte gate. No affinity, power mode, body size,
consumption rate, worker code or SDK runtime is changed.

The frozen v4 slow-consumer minimum is 128 seconds, selected from that
independent control calibration. Small cases remain 30 seconds with 10 seconds
of warmup; other transfers remain 16 seconds. Slow and bulk warmup remains
0.05 seconds. Body size remains 64 MiB, slow consumption 16 MiB/s, and the external
wall guard remains warmup + requested measurement + 90 seconds. Longer slow
windows also make p95 applicable when every round reaches 20 complete replies.

The exact same G0 policy and shared evaluator enforce throughput>=0.85,
CPU seconds per verified byte<=1.25, applicable p95<=1.20 and the original
RSS allowance. Both directions of A/A must pass; each arm must accumulate
at least one observed CPU second across five complete rounds. Failure of
controls or observation quality is ineligible, never a candidate pass.

V3's absolute 2-second observation/window/queue/snapshot/sample-gap ceilings
are unchanged. The 10% window-excess ceiling is also unchanged; the absolute
ceiling still applies and is not scaled up with the longer window. Missing,
nonfinite, unordered or incomplete evidence remains ineligible.

Use `timed_regression_v4.py`. Ten original cases x three interleaved arms x
five fresh processes remain 150 predetermined runs. Freeze the plan and exact
SDK/native, harness, fixture, lock, policy and calibration fingerprints first.
Execute all 150 without retry, replacement or early stopping. Independently
audit every raw worker result, marker, acknowledgement, resource sample and
cleanup record before publication. Preserve old cohorts separately.

The release record must identify the longer-window calibration and its hashes,
the committed v4 harness and the separately frozen production source/native.
This gate covers only the declared Windows AMD64 CPython 3.11 H1/H2 environment;
it does not certify H3, browser impersonation or other platforms' performance.

The initial v4 trial was aborted and remains ineligible: its unchanged
synchronous worker used a fixed 120-second completion barrier, incompatible
with a 128-second requested measurement. No completed or partial round from
that trial is reused. V4.1 sets the internal completion guard to the larger
of 120 seconds and requested seconds + 90. The existing controller wall budget
is unchanged. An accelerated real-thread regression exposes the old failure;
actual 128-second H1 preflights precede the new complete 150. The async workload
and SDK client functions are unchanged, so the independent H2 calibration's
workload is retained. This is an instrument repair, not an SDK runtime change.
