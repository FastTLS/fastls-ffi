# S4 development candidate certification

The reviewed native development input is now core
`cb551d82fe4a3bc51e848b36d22180fb9b0aa798`, ftransport
`d5afcd1a2db48a40850363b76e8a19eb339c757c`, and imitate
`fd10ea4393408e904ae2866ecbfe3efaaaa6ad8e`. `ci/dependencies.json`
selects these immutable checkouts. The standalone module manifest still tests
the published dependency graph independently; a development workspace build
does not turn that graph into a newly published release.

The full native CI retains all six platform/toolchain combinations, source and
isolated Python wheel installs, independent C execution, source and installed
Node packages, licenses, native dependency audits, race and resource gates.
Development snapshots remain non-publishing. The registry versions, ABI and
SDK implementation are unchanged by this certification tooling update.

The published-module notice generator is retained. The bundle additionally
carries exact reviewed LICENSE/NOTICE bytes for the three development source
commits in `ci/reviewed-source-licenses.json`, including imitate's updated
NOTICE. The generator verifies those bytes and refuses a development native
build whose source commits differ from the reviewed materials. Artifact
metadata identifies the actual inputs; supplementary notices do not change a
public module version or claim that the module tag contains newer source.

Manual CI accepts `certification_soak_seconds` from 300 through 7200 seconds.
Invalid or shortened durations fail in dependency resolution before native
jobs start. The default extended observation remains 300 seconds; source and
wheel tests retain their ordinary 30-second observations. S4 requests 3600
seconds for the separate Linux Python extended job using the same hashed native
input as the other stages. Its command deadline is the requested duration plus
180 seconds; the workflow job budget also accommodates the longer run.

Long observations retain concurrency 32 and the same correctness, heap, RSS,
goroutine and handle thresholds. For observations over 300 seconds the recorded
pause between complete batches is one second, rather than the ordinary 0.1
second. This is an explicit long-lifetime resource workload, with bounded
observation memory; it is not a throughput measurement. Samples, actual duration,
requested duration, pause and completed requests remain in the evidence. The
existing baseline GC is retained; no new GC is added during the observation.

This is certification work, not a claim that every gate has passed. Read actual
CI results and artifact hashes. FFI address policy and complete profile.Spec
remain design proposals in CAPABILITY_DESIGN.md. CPU regression acceptance and
formal dependency version selection are separate gates; neither a native build
nor a successful resource observation publishes or approves a release.
