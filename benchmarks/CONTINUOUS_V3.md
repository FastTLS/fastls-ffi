# Independent continuous CPU certification v3

This protocol retains v2's ten cases, deterministic interleaving, five fresh
processes per arm, 30/16/64 second minimum windows and existing wall budgets.
Both controls use the same published SDK/native artifact. Candidate input is
independently pinned. Every run identifier and its order are saved before the
first worker. All 150 runs execute, without retries or replacement rounds.
Original v1/v2 failures and diagnostic R1 evidence remain separate and unchanged.

The G0 performance policy is unchanged: throughput >= 0.85, CPU per verified
business byte <= 1.25, applicable p95 <= 1.20, and the original RSS allowance.
Both directions of A/A must pass. Each arm must accumulate >= 1 observed CPU
second across five rounds. Historical single-transfer absolute baselines are
not interchangeable with continuous workload calibration.

Before any v3 observation, timing eligibility is frozen as follows:

- External CPU window minus worker elapsed must be nonnegative, <= 2 seconds,
  and <= 10% of worker elapsed.
- Each start/stop worker marker to client snapshot delay must be <= 2 seconds.
- Controller marker queue delays, each snapshot call, and consecutive client
  sample gaps (including boundaries) must each be <= 2 seconds.
- Boundary timestamps must be finite, unique and ordered. Samples and snapshot
  costs must be present. Cleanup must complete without killing descendants.

These are additional observation eligibility rules, not relaxed performance
thresholds. R1's 14 independent controls had maximum external window excess
1.235s, stop delay .866s, queue delay .916s and snapshot cost .765s. This prior
diagnostic evidence informs the fixed 2s ceiling; v3 outcomes cannot alter it.
The requested 50ms sampling interval remains a request, not a claim that Windows
process enumeration finishes in 50ms. Actual sample timestamps are retained.
Missing evidence or timing failures make the affected case ineligible; metric
ratios remain visible for diagnosis. A/A or timing failure does not establish
a candidate performance regression.

Use `benchmarks/timed_regression.py` with the locked benchmark environment,
immutable candidate root, historical G0 policy directory and frozen fixture.
Commit the harness first. The plan records all source/runtime/artifact hashes;
the final result checks those fingerprints and the unchanged plan digest.
Only after the complete experiment and all eligibility/performance checks pass
can this Windows AMD64 CPython 3.11 H1/H2 gate be considered satisfied. This
does not certify H3, browser impersonation, other hosts or publication inputs.
