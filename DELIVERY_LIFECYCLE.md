# Connection and publication lifecycle — Python 0.7.1 / Node 0.16.1

The SDK pins Go v0.2.1, profile v0.1.1 and ftransport/imitate v0.1.2. Public ABI v1 and the seven explicit H3 configuration fields remain compatible.

An errored or internally redialed H3 connection generation stops admitting new requests immediately. Pending requests and response bodies retain their own generation until EOF, cancellation or close. Later requests cannot prolong its ownership. The backend's connection pool, bounded retries and direct shared UDP sockets remain in use. The regression holds one valid response while rejecting 20 oversized header responses: only the occupied connection remains, and the peer closes 19 other connections before the held body ends. Both direct and trusted-address paths are covered.

For a formal Python tag at the same certified main commit, the workflow can promote the original platform's native library, header, fixture and audit evidence. It checks the completed source receipt, source digest, dependency graph, compiler/toolchain, build options, host identity and original artifact digests. Missing, stale or unequal inputs fall back to rebuilding and the corresponding checks. The wheel and installation are always checked as the new outer package.

`build-info.json` and Go buildinfo retain the real native producer identity and original version. A promoted wheel also embeds `package-info.json`, binding the outer package version to the producer version, commit/run, target, native digest and source-receipt digest. Release manifests and upload/recovery validation check this relationship; the native metadata is never relabeled as a new build.

npm uses only GitHub Actions OIDC for the six existing packages, with the main `npm-publish.yml` workflow and Environment `npm`. Every prior upload is independently checked against exact registry bytes and signed provenance before it can be skipped. Native packages must be confirmed before the main package is uploaded.

Upload completion, confirmation pending, verification failure and verified completion are distinct states. The immutable manifest/package set and publisher identity bind the saved progress. A recovery restores the original eight-minute absolute read deadline, endpoint/phase counters and at most three publication attempts. Each read call still allows at most five attempts; across those three publication attempts each endpoint/phase permits at most fifteen reads. Expired budgets stop; they cannot be renewed by redispatching. Uploads are never blindly repeated after an uncertain result.

`npm-publish.yml` supports `resume_run_id` and `confirm_only`. Read-only confirmation requires an exact previous run at the same publisher commit and never calls npm publish. Normal resume can continue still-new packages after pending native packages are confirmed. The bounded recovery workflow additionally classifies structured registry propagation failures; authentication, digest/source conflicts and unknown failures require diagnosis.

Progress stays in Actions artifacts. The GitHub Release is prepared as a draft and accepts exactly six archives, the manifest, release notes, SHA256SUMS and the promotion proof. Asset names and digests are checked before publishing the draft; arbitrary progress or other extra assets are rejected.

Test selection records changed files, coverage reasons, source and ref. Isolated SSE/WebSocket adapters can select their resource class plus generic recovery. Shared native/session/dependency/build changes and unknown inputs retain all classes. Formal artifacts still require their shipped platform identities, signatures and actual installs. See [testing policy](TESTING_POLICY.md).
