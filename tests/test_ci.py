"""Exercise the dependency gate through its public CLI, including output safety."""
import os
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ci_runtime import EvidenceRunner
import ci
import development_snapshot
import release_materials
import module_inputs
from package_version import package_version, require_development_version

ROOT = Path(__file__).resolve().parents[1]


class FormalModuleInputs(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        root=Path(self.temporary.name);self.selected=[];self.downloaded={};self.reviewed={}
        for name in ('fastls','ftransport','imitate'):
            path='github.com/FastTLS/'+name;directory=root/name;directory.mkdir()
            (directory/'LICENSE').write_text('actual license\n')
            selected=dict(Path=path,Version='v0.1.1',Sum='h1:actual',GoModSum='h1:mod')
            self.selected.append(selected)
            self.downloaded[(path,'v0.1.1')]=dict(selected,Dir=str(directory),VersionTag=dict(ref='refs/tags/v0.1.1',commit='a'*40),Origin=dict(VCS='git',URL='https://github.com/FastTLS/'+name,
                Hash='a'*40,Ref='refs/tags/v0.1.1'))
            self.reviewed[name+'_ref']='a'*40
    def validate(self):return module_inputs.validate(self.selected,self.downloaded,self.reviewed)
    def test_formal_inputs_bind_versions_sums_commits_and_actual_licenses(self):
        values=self.validate()
        self.assertEqual(module_inputs.source_commits(values),{n:'a'*40 for n in ('fastls','ftransport','imitate')})
        self.assertTrue(all(m['license_files']['LICENSE']==hashlib.sha256(b'actual license\n').hexdigest() for m in values))
    def test_replacements_missing_checksums_and_source_drift_are_rejected(self):
        self.selected[0]['Replace']={'Dir':'local'}
        with self.assertRaisesRegex(RuntimeError,'replacement'):self.validate()
        self.selected[0].pop('Replace');source=self.downloaded[('github.com/FastTLS/fastls','v0.1.1')]
        source.pop('Sum')
        with self.assertRaisesRegex(RuntimeError,'checksums'):self.validate()
        source['Sum']='h1:other'
        with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):self.validate()
        source['Sum']='h1:actual';source['Origin']['Hash']='b'*40
        with self.assertRaisesRegex(RuntimeError,'reviewed source'):self.validate()
    def test_mutable_origins_missing_modules_and_missing_license_are_rejected(self):
        source=self.downloaded[('github.com/FastTLS/fastls','v0.1.1')]
        source['VersionTag']['ref']='refs/heads/main'
        with self.assertRaisesRegex(RuntimeError,'origin/tag'):self.validate()
        source['VersionTag']['ref']='refs/tags/v0.1.1';self.selected.pop()
        with self.assertRaisesRegex(RuntimeError,'required formal'):self.validate()
        self.setUp();source=self.downloaded[('github.com/FastTLS/fastls','v0.1.1')]
        (Path(source['Dir'])/'LICENSE').unlink()
        with self.assertRaisesRegex(RuntimeError,'no license'):self.validate()
    def test_formal_build_never_uses_an_active_workspace(self):
        with self.assertRaisesRegex(RuntimeError,'GOWORK=off'):module_inputs.capture(Path('.'),{'GOWORK':'go.work'})
    def test_cached_branch_origin_requires_an_independently_verified_version_tag(self):
        source=self.downloaded[('github.com/FastTLS/fastls','v0.1.1')]
        source['Origin']['Ref']='refs/heads/reviewed-original-query'
        self.assertTrue(self.validate())
        source.pop('VersionTag')
        with self.assertRaisesRegex(RuntimeError,'origin/tag'):self.validate()
    def test_node_revision_uses_formal_module_origin_without_git_in_module_cache(self):
        import runpy
        resolve=runpy.run_path(str(ROOT/'node/scripts/native-metadata.py'))['module_revision']
        module=dict(Path='github.com/FastTLS/fastls/profile',Version='v0.1.0',Sum='h1:profile',Dir='not-a-git-checkout')
        info=dict(local_dependencies=False,module_graph=[dict(module,Origin={'Hash':'a'*40})])
        self.assertEqual(resolve(module,info),'a'*40)
        info['module_graph'][0]['Sum']='h1:changed'
        with self.assertRaisesRegex(ValueError,'formal module differs'):resolve(module,info)


class DependencyGate(unittest.TestCase):
    def test_release_mode_cannot_select_local_or_unknown_dependencies(self):
        self.assertEqual(ci.dependency_mode({'FASTLS_DEPENDENCY_MODE':'formal','FASTLS_RELEASE_MODE':'true'}),'formal')
        for mode in ('local','unknown'):
            with self.assertRaises(ValueError):ci.dependency_mode({'FASTLS_DEPENDENCY_MODE':mode,'FASTLS_RELEASE_MODE':'true'})
        process=subprocess.run([sys.executable,'scripts/build.py','--local'],cwd=ROOT,
            env={**os.environ,'FASTLS_RELEASE_MODE':'true'},capture_output=True,text=True)
        self.assertNotEqual(process.returncode,0)
        self.assertIn('release builds require formal',process.stderr)
    def test_soak_duration_rejects_invalid_or_shortened_certification(self):
        self.assertEqual(ci.extended_soak_seconds({}), 300)
        self.assertEqual(ci.extended_soak_seconds({"FASTLS_EXTENDED_SOAK_SECONDS": "3600"}), 3600)
        self.assertEqual(ci.extended_soak_seconds({"FASTLS_EXTENDED_SOAK_SECONDS": "7200"}), 7200)
        for value in ("0", "299", "7201", "3600.0", "3600\nother=1", "-300"):
            with self.assertRaises(ValueError):
                ci.extended_soak_seconds({"FASTLS_EXTENDED_SOAK_SECONDS": value})

    def test_preflight_can_be_loaded_without_scripts_on_import_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            code = "import runpy; assert callable(runpy.run_path(" + repr(str(ROOT / "scripts/ci.py")) + ")[\"preflight\"])"
            process = subprocess.run([sys.executable, "-I", "-c", code], cwd=temporary, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def resolve(self, overrides):
        env = os.environ.copy()
        env.pop("GITHUB_STEP_SUMMARY", None)
        for key in ("FASTLS_REF", "FTRANSPORT_REF", "IMITATE_REF"):
            env[key] = ""
        env.update(overrides)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            env["GITHUB_OUTPUT"] = str(output)
            process = subprocess.run([sys.executable, "scripts/ci.py", "resolve"], cwd=ROOT, env=env, text=True, capture_output=True)
            return process, output.read_text() if output.is_file() else ""

    def test_reviewed_commit_outputs(self):
        process, output = self.resolve({**{key: "a" * 40 for key in ("FASTLS_REF", "FTRANSPORT_REF", "IMITATE_REF")}, 'FASTLS_DEPENDENCY_MODE':'local'})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(output.splitlines()[:3], [f"{name}_ref={'a' * 40}" for name in ("fastls", "ftransport", "imitate")])
        matrix = json.loads(output.splitlines()[3].removeprefix("matrix="))
        self.assertEqual(len(matrix), 6)
        self.assertEqual({c["target"] for c in matrix}, {"windows-amd64", "linux-amd64", "linux-arm64", "darwin-amd64", "darwin-arm64"})

    def test_formal_graph_override_cannot_change_the_committed_version_lock(self):
        process,output=self.resolve({'FASTLS_REF':'a'*40,'FASTLS_DEPENDENCY_MODE':'formal'})
        self.assertNotEqual(process.returncode,0)
        self.assertEqual(output,'')
        self.assertIn('committed version lock',process.stdout)

    def test_branch_or_injected_output_is_rejected(self):
        for value in ("main", "a" * 40 + "\nother=value"):
            process, output = self.resolve({"FASTLS_REF": value})
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(output, "")


class ReviewedSourceLicenses(unittest.TestCase):
    def test_actual_native_sources_and_license_bytes_must_match_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'ci').mkdir();(root/'dist').mkdir()
            text='reviewed source notice\n';commit='a'*40
            record={'sources':{'imitate':{'commit':commit,'files':{'NOTICE.md':{
                'text':text,'normalized_sha256':hashlib.sha256(text.encode()).hexdigest()}}}}}
            path=root/'ci/reviewed-source-licenses.json';path.write_text(json.dumps(record))
            info=root/'dist/build-info.json';info.write_text(json.dumps({'local_dependencies':True,'sources':{'imitate':commit}}))
            with patch.object(release_materials,'ROOT',root):
                self.assertIn('reviewed source notice','\n'.join(release_materials.reviewed_source_notices()))
                info.write_text(json.dumps({'local_dependencies':True,'sources':{'imitate':'b'*40}}))
                with self.assertRaisesRegex(RuntimeError,'actual native input'):release_materials.reviewed_source_notices()
                info.write_text(json.dumps({'local_dependencies':True,'sources':{'imitate':commit}}))
                record['sources']['imitate']['files']['NOTICE.md']['text']='changed'
                path.write_text(json.dumps(record))
                with self.assertRaisesRegex(RuntimeError,'bytes changed'):release_materials.reviewed_source_notices()

    def test_formal_module_notices_are_bound_to_downloaded_source_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'ci').mkdir();(root/'dist').mkdir()
            text='reviewed notice\n';digest=hashlib.sha256(text.encode()).hexdigest();commit='a'*40
            review={'sources':{'imitate':{'commit':commit,'files':{'NOTICE.md':{'text':text,'normalized_sha256':digest}}}}}
            (root/'ci/reviewed-source-licenses.json').write_text(json.dumps(review))
            info=dict(local_dependencies=False,sources={'imitate':commit},module_graph=[dict(Path='github.com/FastTLS/imitate',license_files={'NOTICE.md':digest})])
            destination=root/'dist/build-info.json';destination.write_text(json.dumps(info))
            with patch.object(release_materials,'ROOT',root):
                self.assertIn('reviewed notice','\n'.join(release_materials.reviewed_source_notices()))
                info['module_graph'][0]['license_files']['NOTICE.md']='changed';destination.write_text(json.dumps(info))
                with self.assertRaisesRegex(RuntimeError,'formal module license'):release_materials.reviewed_source_notices()


class PackageVersion(unittest.TestCase):
    def test_development_override_is_validated(self):
        self.assertEqual(package_version({"FASTLS_PACKAGE_VERSION": "0.1.0.dev123"}), "0.1.0.dev123")
        self.assertEqual(package_version({"FASTLS_PACKAGE_VERSION": "v0.1.0", "FASTLS_RELEASE_MODE": "true"}), "0.1.0")
        self.assertEqual(require_development_version({"FASTLS_PACKAGE_VERSION": "0.1.0.dev123+gabc"}), "0.1.0.dev123+gabc")
        for value in ("0.1.0", "main", "0.1.0.dev1\nother"):
            with self.assertRaises(RuntimeError):
                require_development_version({"FASTLS_PACKAGE_VERSION": value})

    def test_snapshot_manifest_is_non_publishing_and_hashed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            sources = {"fastls-ffi": "a" * 40, "fastls": "b" * 40}
            (dist / "ci-inputs.json").write_text(json.dumps({
                "target": "windows-amd64", "sources": sources,
                "package_version": "0.1.0.dev123",
            }))
            for name in ("fastls-0.1.0.dev123-py3-none-win_amd64.whl",
                         "fastls-0.1.0.dev123.tar.gz", "native-audit.json"):
                (dist / name).write_bytes(name.encode())
            (dist / "build-info.json").write_text(json.dumps({"package_version": "0.1.0.dev123"}))
            destination = development_snapshot.create_manifest(
                root, {"FASTLS_PACKAGE_VERSION": "0.1.0.dev123"})
            manifest = json.loads(destination.read_text())
            self.assertEqual(manifest["status"], "development-only")
            self.assertFalse(manifest["publishable"])
            self.assertEqual(manifest["sources"], sources)
            self.assertEqual(set(manifest["files"]), {
                "fastls-0.1.0.dev123-py3-none-win_amd64.whl",
                "fastls-0.1.0.dev123.tar.gz", "build-info.json", "native-audit.json",
            })

    def test_formal_manifest_is_publishable_only_in_release_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist = root / "dist"
            dist.mkdir()
            sources = {"fastls-ffi": "a" * 40, "fastls": "b" * 40}
            (dist / "ci-inputs.json").write_text(json.dumps({
                "target": "linux-amd64", "sources": sources, "package_version": "0.1.0",
            }))
            for name in ("fastls-0.1.0-py3-none-manylinux_2_39_x86_64.whl",
                         "fastls-0.1.0.tar.gz", "native-audit.json"):
                (dist / name).write_bytes(name.encode())
            (dist / "build-info.json").write_text(json.dumps({"package_version": "0.1.0"}))
            destination = development_snapshot.create_manifest(root, {
                "FASTLS_PACKAGE_VERSION": "v0.1.0", "FASTLS_RELEASE_MODE": "true",
            })
            manifest = json.loads(destination.read_text())
            self.assertEqual(destination.name, "release-manifest.json")
            self.assertEqual(manifest["status"], "official")
            self.assertTrue(manifest["publishable"])
            self.assertEqual(manifest["package_version"], "0.1.0")


class ExecutionEvidence(unittest.TestCase):
    def test_failure_preserves_earlier_results_and_partial_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = EvidenceRunner(Path(temporary) / "evidence", {"stage": "test"})
            runner.execute("passed", [sys.executable, "-c", "print('first')"], ROOT, os.environ.copy())
            with self.assertRaises(RuntimeError):
                runner.execute("failed", [sys.executable, "-c", "print('partial'); raise SystemExit(7)"], ROOT, os.environ.copy())
            record = json.loads((runner.directory / "results.json").read_text())
            self.assertFalse(record["success"])
            self.assertEqual([c["exit_code"] for c in record["checks"]], [0, 7])
            self.assertIn("partial", (runner.directory / "failed.log").read_text())

    def test_stalled_process_is_terminated_and_marked_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = EvidenceRunner(Path(temporary) / "evidence", {})
            with self.assertRaises(RuntimeError):
                runner.execute("timeout", [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(30)"], ROOT, os.environ.copy(), timeout=.5)
            self.assertEqual(runner.results["checks"][0]["state"], "failed")
            self.assertLess(runner.results["checks"][0]["duration_seconds"], 10)

    def test_changed_sdk_artifact_or_commit_is_rejected(self):
        sources = {"fastls-ffi": "a" * 40}
        record = dict(target="windows-amd64", sources=sources, package_version=package_version(),
                      sdk={"sdk.py": "one"}, artifacts={"lib": "one"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "dist").mkdir()
            (root / "dist/ci-inputs.json").write_text(json.dumps(record))
            (root / 'ci').mkdir()
            (root / 'ci/dependencies.json').write_text(json.dumps({'build_mode':'local'}))
            with patch.object(ci, "ROOT", root), patch.object(ci, "sdk_inputs", return_value=record["sdk"]) as sdk, patch.object(ci, "artifact_inputs", return_value=record["artifacts"]) as artifacts:
                ci.verify_inputs("windows-amd64", sources)
                for target, revisions in (("linux-amd64", sources), ("windows-amd64", {"fastls-ffi": "b" * 40})):
                    with self.assertRaises(RuntimeError): ci.verify_inputs(target, revisions)
                sdk.return_value = {"sdk.py": "changed"}
                with self.assertRaises(RuntimeError): ci.verify_inputs("windows-amd64", sources)
                sdk.return_value = record["sdk"]
                artifacts.return_value = {"lib": "changed"}
                with self.assertRaises(RuntimeError): ci.verify_inputs("windows-amd64", sources)
                artifacts.return_value = record["artifacts"]
                with patch.dict(os.environ, {"FASTLS_PACKAGE_VERSION": "0.1.0.dev999"}):
                    with self.assertRaises(RuntimeError): ci.verify_inputs("windows-amd64", sources)
                (root/'ci/dependencies.json').write_text(json.dumps({'build_mode':'formal'}))
                provenance=dict(local_dependencies=False,sources=sources,module_graph=[{'Path':'verified'}])
                (root/'dist/build-info.json').write_text(json.dumps(provenance))
                ci.verify_inputs('windows-amd64',sources)
                provenance['sources']={'fastls-ffi':'b'*40}
                (root/'dist/build-info.json').write_text(json.dumps(provenance))
                with self.assertRaisesRegex(RuntimeError,'formal native provenance'):ci.verify_inputs('windows-amd64',sources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
