# Formal module builds

The release candidate binds published `fastls v0.1.2`, `ftransport v0.1.1`,
`imitate v0.1.1` and `profile v0.1.0`. Python 0.1.5 and Node 0.10.2 are candidate
versions until their exact artifacts complete certification and publication.
The SDK ABI and request behavior are unchanged by this build transition.

`ci/dependencies.json` selects `build_mode: formal`. Native compilation and
fixtures use `GOWORK=off`; sibling checkouts remain available for explicit
upstream regression tests, but never replace the compiled dependencies.
The public graph rejects `replace`, missing checksums and retired fhttp.
Formal CI uses the committed dependency lock, without repository-variable
fallback; explicit source overrides must equal that lock. Changed module
versions require a reviewed source/configuration commit, not an input override.
`scripts/build.py --local` remains an explicit development-only path.

Formal builds verify the module cache before and after compilation and bind
the actual Go-selected versions, module and go.mod checksums, origin tags,
source commits and license file hashes in `dist/module-inputs.json` and
`dist/build-info.json`. First-party origins must match the reviewed commit
lock. The root go.mod/go.sum hashes and library hash are also recorded.
Do not edit these generated files to change provenance or remove a blocker.

Python notice checks compare reviewed license bytes to the downloaded formal
modules. Node packaging reads module origin metadata without assuming that a
module-cache directory is a Git checkout. Independent CI consumer jobs verify
the original native inputs; changed SDK, version, graph or artifact hashes
reject reuse. The performance harness accepts formal candidates only with
their exact source, graph and manifest hashes intact.

Build the committed source with `python scripts/build.py`; the compiler and
package version can be selected with the existing explicit settings. Build
and validate the fixture, Python/C consumers, all Node package forms and the
applicable platform matrices using `scripts/ci.py`. Development CI snapshots
carry a `.devN` Python version. A green development graph or a published
upstream tag does not certify new SDK artifacts or authorize mixing runs.

The earlier S4/R2 performance results remain evidence for their original
inputs. New formal-graph artifacts require a new frozen complete experiment
and applicable long-running checks before the SDK/service release stage.
SDK address policy and full profile.Spec remain separate planned work.
