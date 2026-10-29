"""Verify full-tree source identity before reusing published candidate bytes."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from promotion_source import certify_source


class Promotion(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'baseline').write_text('immutable')
        self.base = self.commit('baseline')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True).strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def test_publishing_only_commit_keeps_original_producer(self):
        path = self.root / 'node/scripts/check-npm-oidc.mjs'
        path.parent.mkdir(parents=True)
        path.write_text('correct identity verification')
        head = self.commit('publisher')
        proof = certify_source(self.base, head, self.root)
        self.assertEqual(proof['producer_revision'], self.base)
        self.assertEqual(proof['publisher_revision'], head)
        self.assertEqual(proof['changed_publishing_tools'], ['node/scripts/check-npm-oidc.mjs'])
        self.assertTrue(proof['production_inputs_unchanged'])

    def test_runtime_dependency_version_build_and_unknown_changes_are_rejected(self):
        for filename in ('node/src/session.ts', 'node/package.json', 'go.mod', 'scripts/build.py', 'unknown'):
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('changed')
            head = self.commit('changed input')
            with self.assertRaisesRegex(ValueError, 'SDK, dependency, version or native build'):
                certify_source(self.base, head, self.root)

    def test_invalid_or_unrelated_commits_are_rejected(self):
        with self.assertRaises(ValueError): certify_source('invalid', self.base, self.root)
        path = self.root / 'node/scripts/check-npm-oidc.mjs'
        path.parent.mkdir(parents=True)
        path.write_text('publisher')
        head = self.commit('publisher')
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            certify_source(head, self.base, self.root)


if __name__ == '__main__': unittest.main()
