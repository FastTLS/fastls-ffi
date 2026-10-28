"""Coverage must follow changes, and required failures must never become skips."""
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_plan


class Selection(unittest.TestCase):
    def test_docs_avoid_native_and_node(self):
        plan = test_plan.choose(['README.md', 'docs/install.md'])
        self.assertEqual(plan['tier'], 'docs')
        self.assertFalse(any(plan[k] for k in ('native', 'node', 'pypi', 'candidates', 'node_soak')))

    def test_python_patch_uses_two_platforms_without_resource_or_performance_campaign(self):
        plan = test_plan.choose(['python/fastls/api.py'])
        self.assertEqual(plan['tier'], 'patch')
        self.assertEqual({t['target'] for t in plan['matrix']}, {'windows-amd64', 'linux-amd64'})
        self.assertEqual(plan['extended'], [])
        self.assertFalse(plan['full_performance'])

    def test_patch_release_still_covers_all_five_published_platforms(self):
        plan = test_plan.choose(['python/fastls/api.py'], release='v0.1.6', previous='v0.1.5')
        self.assertEqual(plan['tier'], 'patch')
        self.assertEqual(len(plan['matrix']), 5)
        self.assertEqual(plan['extended'], ['race'])
        self.assertTrue(plan['pypi'] and plan['candidates'] and plan['node'])
        self.assertFalse(plan['node_soak'])

    def test_minor_and_major_keep_distinct_compatibility_and_soak_scopes(self):
        minor = test_plan.choose(['README.md'], release='v0.2.0', previous='v0.1.5')
        major = test_plan.choose(['README.md'], release='v1.0.0', previous='v0.1.5')
        self.assertEqual((minor['tier'], len(minor['matrix']), minor['soak_seconds']), ('minor', 5, 300))
        self.assertEqual((major['tier'], len(major['matrix']), major['soak_seconds']), ('major', 6, 3600))
        self.assertEqual(major['node_matrix'][-1]['node'], '22')

    def test_risk_cannot_be_downgraded_by_patch_override(self):
        for filename in ('internal/bridge/session.go', 'go.mod', 'ci/dependencies.json',
                         'scripts/build.py', 'python/fastls/proxy.py', 'LICENSE'):
            with self.subTest(filename=filename):
                self.assertEqual(test_plan.choose([filename], requested='patch')['tier'], 'minor')

    def test_missing_baseline_and_unclassified_files_expand_coverage(self):
        for options in ({'uncertain': True}, {'override': True}):
            self.assertEqual(test_plan.choose(['README.md'], **options)['tier'], 'minor')
        self.assertEqual(test_plan.choose(['unclassified/new.bin'])['tier'], 'minor')

    def test_manual_major_and_first_release_are_full(self):
        self.assertEqual(test_plan.choose(['README.md'], requested='major')['tier'], 'major')
        self.assertEqual(test_plan.choose([], release='v0.1.0')['tier'], 'major')

    def test_invalid_and_nonadvancing_versions_fail(self):
        for current, previous in (('v0.1.5', 'v0.1.5'), ('v0.1.4', 'v0.1.5'), ('v1.0', 'v0.1.5')):
            with self.subTest(current=current):
                with self.assertRaises(ValueError):
                    test_plan.release_tier(current, previous)

    def test_node_patch_runs_node_without_full_campaign(self):
        plan = test_plan.choose(['node/src/index.ts'])
        self.assertTrue(plan['node'])
        self.assertFalse(plan['node_soak'] or plan['candidates'])

    def test_pr_uses_merge_base_and_push_uses_complete_before_range(self):
        for event, expected in (({'before': 'a' * 40}, ('diff', '--name-only', '--no-renames', 'a' * 40, 'HEAD')),
                                ({'pull_request': {'base': {'sha': 'b' * 40}}}, ('diff', '--name-only', '--no-renames', 'merge-base-value', 'HEAD'))):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'event.json';path.write_text(json.dumps(event))
                calls = []
                def fake(*args):
                    calls.append(args)
                    return 'merge-base-value' if args[0] == 'merge-base' else 'README.md'
                with patch.object(test_plan, 'git', side_effect=fake):
                    self.assertEqual(test_plan.context({'GITHUB_EVENT_PATH': str(path)})['tier'], 'docs')
                self.assertIn(expected, calls)

    def test_outputs_are_valid_job_inputs_without_extra_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'output'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(path)}, clear=True), patch.object(test_plan, 'context', return_value=test_plan.choose(['README.md'])), patch.object(sys, 'argv', ['test_plan.py']):
                with patch('sys.stdout', new_callable=io.StringIO):
                    test_plan.main()
            output = dict(line.split('=', 1) for line in path.read_text().splitlines())
            self.assertEqual(output['tier'], 'docs')
            self.assertEqual(output['native'], 'false')
            self.assertEqual(json.loads(output['plan'])['tier'], 'docs')

    def test_node_version_bump_requires_all_published_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);(root / 'node').mkdir()
            (root / 'node/package.json').write_text(json.dumps({'version': '0.10.3'}))
            event = root / 'event.json';event.write_text(json.dumps({'before': 'a' * 40}))
            def fake(*args):
                return json.dumps({'version': '0.10.2'}) if args[0] == 'show' else 'node/package.json'
            with patch.object(test_plan, 'ROOT', root), patch.object(test_plan, 'git', side_effect=fake):
                plan = test_plan.context({'GITHUB_EVENT_PATH': str(event)})
            self.assertEqual(plan['tier'], 'patch')
            self.assertEqual(len(plan['matrix']), 5)
            self.assertTrue(plan['candidates'])


class Gate(unittest.TestCase):
    def results(self, plan):
        enabled = {'dependencies': True, 'native': plan['native'], 'tests': plan['native'], 'extended': bool(plan['extended']),
                   'node': plan['node'], 'node-soak': plan['node_soak'], 'node-candidates': plan['candidates']}
        return {k: {'result': 'success' if v else 'skipped'} for k, v in enabled.items()}

    def test_all_planned_scopes_accept_only_explicit_optional_skips(self):
        for tier in ('docs', 'patch', 'minor', 'major'):
            plan = test_plan.choose(['README.md'], **({'requested': tier} if tier != 'docs' else {}))
            self.assertEqual(test_plan.required_results(plan, self.results(plan)), [])

    def test_failure_cancellation_and_missing_results_never_pass(self):
        plan = test_plan.choose(['python/fastls/api.py'])
        for result in ('failure', 'cancelled', 'skipped', None):
            values = self.results(plan);values['tests']['result'] = result
            self.assertTrue(test_plan.required_results(plan, values))
        values = self.results(plan);del values['dependencies']
        self.assertTrue(test_plan.required_results(plan, values))

    def test_failed_optional_job_cannot_be_hidden(self):
        plan = test_plan.choose(['README.md']);values = self.results(plan)
        values['node']['result'] = 'failure'
        self.assertTrue(test_plan.required_results(plan, values))

    def test_public_cli_rejects_a_cancelled_required_job(self):
        plan = test_plan.choose(['node/src/index.ts']);values = self.results(plan)
        values['node']['result'] = 'cancelled'
        process = subprocess.run([sys.executable, str(Path(test_plan.__file__)), '--check-results'],
                                 env={**os.environ, 'TEST_PLAN': json.dumps(plan), 'TEST_RESULTS': json.dumps(values)}, capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('node: expected success, got cancelled', process.stderr)


if __name__ == '__main__':
    unittest.main()
