# G1 source integration status

Date: 2026-10-27. This main-branch integration includes the G0 baseline tools,
response byte-contract checks, interleaved performance regression driver, and
Windows CPU accounting diagnostics. It is source integration, not a new SDK
release or a completed performance certification.

## Tested development inputs

- Fastls: `cb551d82fe4a3bc51e848b36d22180fb9b0aa798`.
- ftransport: `d5afcd1a2db48a40850363b76e8a19eb339c757c`.
- FFI runtime/test baseline: `9b0cd2bfb3dac9a7b556a4d98307faa6973ddd6b`.
- Performance driver: `6b3c357` and CPU diagnostic driver: `4155bd8`.
- imitate: `fd10ea4393408e904ae2866ecbfe3efaaaa6ad8e`.

The tested Windows AMD64 / Go 1.26.10 / CPython 3.11.9 candidate passed full
core/transport/FFI tests and race checks, public and explicit development
dependency checks, 31 Python SDK/network/stability tests, 71 smoke runs, and
50 mixed resource rounds for each of H1 and H2. These are local observations;
they do not certify other platforms or new dependency combinations.

## Performance remains open

The complete three-arm, five-round experiment had 150 valid runs across ten
selected H1/H2 workloads: six workloads passed, three were ineligible because
the published package's A/A CPU check was unstable, and H1 gzip exceeded the
CPU-seconds/business-byte limit (1.286 versus a maximum ratio of 1.25).

A separate 75-run diagnostic compared process CPU seconds and raw Windows
process cycle counts. Its published-package A/A CPU ratios varied from 0.728
to 1.446, while cycle/byte ratios were 0.965 to 1.009. Candidate cycle/byte
ratios were 0.962 to 1.038. This supports an accounting/observability issue in
these short or low-duty measurements; it does not prove an absence of a CPU
regression. Cycles were not converted to seconds or substituted into the
original gate. All original failures and thresholds remain recorded.

Next, establish a repeatable CPU measurement window and independent controls,
then rerun the affected acceptance checks without discarding earlier results
or relaxing thresholds. Service integration, the remote platform/toolchain
matrix and longer resource observations remain separate follow-up work.

## Dependency and release boundaries

Production go.mod requirements and `ci/dependencies.json` remain pinned to
their existing published/tested inputs. A default main checkout therefore
does not automatically use the new core and transport commits above; use an
explicit reviewed development workspace when validating that combination.
The ignored local `go.work` is not distributed as a dependency override.

The Go H2 SETTINGS ACK overlay in `scripts/build_benchmark_fixture.py` is only
for the audited benchmark fixture. It is not applied to the SDK, GOROOT, or
production services. No new SDK H3/address-policy API, tag, registry package,
or GitHub release is included in this integration. Published Python 0.1.4 and
Node 0.10.1 installation instructions remain applicable to those releases.

See [benchmark instructions](BENCHMARKS.md) and the historical
[phase plan](NEXT_PHASE.md) for the surrounding work.
