"""A tag cannot borrow resource evidence from other sources or incomplete checks."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import release_reuse
import test_plan
from ci import TARGETS


class ReleaseReuse(unittest.TestCase):
    def setUp(self):
        self.run = dict(id=12, status='completed', conclusion='success', head_sha='a' * 40,
            head_branch='main', event='push', path='.github/workflows/ci.yml',
            repository={'full_name': 'FastTLS/fastls-ffi'}, html_url='https://github.com/FastTLS/fastls-ffi/actions/runs/12')
        names = ['dependencies', 'All native tests', 'race / linux-amd64', 'soak / linux-amd64']
        names += ['Resource recovery / ' + name for name in ('generic', 'duplex', 'sse', 'proxy', 'ws')]
        for target in TARGETS[:5]:
            label = f"{target['target']}-go{target['go']}-py{target['python']}"
            names += ['Build / ' + label, 'Python checks / ' + label,
                      f"Node checks / {target['target']} / Go {target['go']} / Node 24",
                      'Candidate / ' + label + '-node24']
        self.jobs = [dict(name=name, status='completed', conclusion='success') for name in names]

    def verify(self):
        return release_reuse.verify(self.run, self.jobs, 'FastTLS/fastls-ffi', 'a' * 40)

    def test_exact_success_is_reusable(self):
        self.assertEqual(self.verify()['run_id'], 12)

    def test_source_workflow_and_overall_failure_rejected(self):
        for key, value in dict(head_sha='b' * 40, head_branch='other', event='workflow_dispatch',
                path='.github/workflows/other.yml', conclusion='failure', status='in_progress',
                repository={'full_name': 'other/fastls-ffi'}).items():
            with self.subTest(key=key), patch.dict(self.run, {key: value}):
                with self.assertRaises(ValueError):
                    self.verify()

    def test_missing_duplicate_failed_cancelled_or_skipped_jobs_rejected(self):
        for name in [job['name'] for job in self.jobs]:
            original = json.loads(json.dumps(self.jobs))
            index = next(i for i, job in enumerate(self.jobs) if job['name'] == name)
            for conclusion in ('failure', 'cancelled', 'skipped', None):
                with self.subTest(name=name, conclusion=conclusion):
                    self.jobs[index]['conclusion'] = conclusion
                    with self.assertRaises(ValueError):
                        self.verify()
            self.jobs = original
            self.jobs.append(dict(self.jobs[index]))
            with self.assertRaises(ValueError):
                self.verify()
            self.jobs = [job for job in original if job['name'] != name]
            with self.assertRaises(ValueError):
                self.verify()
            self.jobs = original

    def test_verified_tag_retains_platform_installs_and_gate(self):
        env = dict(GITHUB_ACTIONS='true', GITHUB_REF_TYPE='tag', GITHUB_REF_NAME='v0.2.0')
        def git(*args):
            return 'v0.1.5' if args[0] == 'tag' else 'python/VERSION'
        with patch.object(test_plan, 'git', side_effect=git), patch.object(release_reuse, 'find', return_value=self.verify()):
            plan = test_plan.context(env)
        self.assertEqual(plan['tier'], 'minor')
        self.assertEqual(plan['extended'], [])
        self.assertFalse(plan['node_soak'])
        self.assertEqual(len(plan['matrix']), 5)
        self.assertTrue(all(plan[k] for k in ('native', 'pypi')))
        self.assertFalse(plan['node'] or plan['candidates'])
        self.assertTrue(test_plan.required_results(plan, {}))

    def test_unavailable_evidence_falls_back_and_major_or_override_does_not_reuse(self):
        env = dict(GITHUB_ACTIONS='true', GITHUB_REF_TYPE='tag', GITHUB_REF_NAME='v0.2.0')
        def git(*args):
            return 'v0.1.5' if args[0] == 'tag' else 'python/VERSION'
        for result in (None, OSError('offline')):
            with patch.object(test_plan, 'git', side_effect=git), patch.object(release_reuse, 'find', **(
                    {'side_effect': result} if isinstance(result, Exception) else {'return_value': result})):
                plan = test_plan.context(env)
                self.assertEqual(plan['extended'], ['race', 'soak'])
                self.assertTrue(plan['node_soak'])
        for extra in ({'TEST_TIER': 'major'}, {'CERTIFICATION_SOAK_SECONDS': '300'}):
            with patch.object(test_plan, 'git', side_effect=git), patch.object(release_reuse, 'find') as find:
                test_plan.context({**env, **extra})
                find.assert_not_called()

    def test_individual_resource_evidence_does_not_turn_failed_node_checks_green(self):
        self.run['conclusion'] = 'failure'
        for job in self.jobs:
            if job['name'] == 'All native tests' or job['name'].startswith('Node checks /'):
                job['conclusion'] = 'failure'
            elif job['name'].startswith('Candidate /'):
                job['conclusion'] = 'skipped'
        with self.assertRaises(ValueError):
            self.verify()
        reused = release_reuse.verify(self.run, self.jobs, 'FastTLS/fastls-ffi', 'a' * 40, resource_only=True)
        self.assertTrue(reused['individual_resource_checks_only'])
        for job in self.jobs:
            if job['name'].startswith(('race /', 'soak /', 'Resource recovery /', 'Build /')):
                before = job['conclusion']
                job['conclusion'] = 'failure'
                with self.assertRaises(ValueError):
                    release_reuse.verify(self.run, self.jobs, 'FastTLS/fastls-ffi', 'a' * 40, resource_only=True)
                job['conclusion'] = before

    def test_only_reviewed_infrastructure_paths_can_reuse_ancestor_resources(self):
        for changed in ('node/scripts/check-parity.mjs\nci/resource-reuse.json', '',
                        'internal/bridge/session.go', 'go.sum', 'python/fastls/_native.py',
                        'node/src/session.ts', '.github/workflows/ci.yml', 'tests/capability_resource_soak.py'):
            with patch.object(release_reuse.subprocess, 'run') as run, patch.object(
                    release_reuse.subprocess, 'check_output', return_value=changed):
                run.return_value.returncode = 0
                result = release_reuse.equivalent_resource_inputs('a' * 40, 'b' * 40)
                self.assertEqual(result is not None, set(changed.splitlines()) <= release_reuse.INFRASTRUCTURE_ONLY)
                run.return_value.returncode = 1
                self.assertIsNone(release_reuse.equivalent_resource_inputs('a' * 40, 'b' * 40))
        self.assertIsNone(release_reuse.equivalent_resource_inputs('not-a-sha', 'b' * 40))

    def test_main_node_delivery_requires_all_fresh_platform_packages_and_a_green_gate(self):
        for job in self.jobs:
            if job['name'].startswith(('Resource recovery /', 'race /', 'soak /')):
                job['conclusion'] = 'skipped'
        result = release_reuse.verify(self.run, self.jobs, 'FastTLS/fastls-ffi', 'a' * 40, platform_only=True)
        self.assertTrue(result['platform_delivery_only'])
        for name in ('All native tests', 'Node checks / linux-amd64 / Go 1.26.10 / Node 24',
                     'Candidate / darwin-arm64-go1.26.10-py3.11-node24'):
            job = next(job for job in self.jobs if job['name'] == name)
            job['conclusion'] = 'failure'
            with self.assertRaises(ValueError):
                release_reuse.verify(self.run, self.jobs, 'FastTLS/fastls-ffi', 'a' * 40, platform_only=True)
            job['conclusion'] = 'success'

    def test_missing_same_commit_node_delivery_keeps_tag_node_checks(self):
        def git(*args):
            return 'v0.1.5' if args[0] == 'tag' else 'python/VERSION'
        with patch.object(test_plan, 'git', side_effect=git), patch.object(release_reuse, 'find',
                side_effect=[self.verify(), None]):
            plan = test_plan.context(dict(GITHUB_ACTIONS='true', GITHUB_REF_TYPE='tag', GITHUB_REF_NAME='v0.2.0'))
        self.assertTrue(plan['node'] and plan['candidates'])


if __name__ == '__main__':
    unittest.main()
