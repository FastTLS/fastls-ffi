"""Publication repairs must not silently change native test configuration."""
from pathlib import Path
import sys
import json
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from promote_tag import workflow_inputs_unchanged, INPUT, PUSH, PRODUCTION_JOBS


class PublicationWorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.before = 'name: CI\nenv:\n  COMPILER: pinned\njobs:\n'
        self.before += ''.join('  '+name+':\n    run: frozen\n' for name in sorted(PRODUCTION_JOBS))
        self.before += "  resume-pypi:\n    run: old\n'on':\n  push: null\n  pull_request: null\n  workflow_dispatch:\n    inputs:\n"
        self.after = self.before.replace('    run: old', '    run: promote-original-artifacts')
        self.after = self.after.replace('  push: null\n', PUSH) + INPUT

    def test_explicit_publication_recovery_preserves_native_inputs(self):
        self.assertTrue(workflow_inputs_unchanged(self.before, self.after))
        self.assertTrue(workflow_inputs_unchanged(self.before.rstrip(), self.after.rstrip()))
        self.assertTrue(workflow_inputs_unchanged(self.before, self.before))

    def test_environment_native_jobs_or_existing_event_changes_are_rejected(self):
        for changed in (self.after.replace('COMPILER: pinned', 'COMPILER: unknown'),
                        self.after.replace('run: frozen', 'run: changed', 1),
                        self.after.replace("tags: ['v*']", "tags: ['v0.2.*']"),
                        self.after.replace('pull_request: null', 'pull_request: changed'),
                        self.after.replace('  native:', '  renamed-native:')):
            self.assertFalse(workflow_inputs_unchanged(self.before, changed))

    def test_unknown_workflow_structure_is_not_eligible_for_reuse(self):
        self.assertFalse(workflow_inputs_unchanged('unknown', self.after))
        self.assertFalse(workflow_inputs_unchanged(self.before, 'unknown'))

    def test_planner_reuses_only_proven_publication_workflow_changes(self):
        import test_plan
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'before': 'a' * 40}))
            for after, expected_native in ((self.after, False), (self.after.replace('run: frozen', 'run: changed', 1), True)):
                def git(*args):
                    if args[0] == 'diff': return '.github/workflows/ci.yml\nscripts/promote_tag.py\nscripts/test_plan.py'
                    if args[0] == 'show': return after if args[1].startswith('HEAD:') else self.before
                    raise AssertionError(args)
                with patch.object(test_plan, 'git', side_effect=git):
                    plan = test_plan.context({'GITHUB_EVENT_PATH': str(event)})
                self.assertEqual(plan['native'], expected_native)
                self.assertIn('.github/workflows/ci.yml', plan['files'])
