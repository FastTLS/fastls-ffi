import copy
import unittest
import urllib.request
from recover_npm_run import recovery_plan,NoCredentialRedirect


class Recover(unittest.TestCase):
    def setUp(self):
        self.sha='a'*40
        self.run=dict(id=1,repository=dict(full_name='FastTLS/fastls-ffi'),event='workflow_dispatch',
            head_branch='main',head_sha=self.sha,path='.github/workflows/npm-publish.yml',status='completed',conclusion='failure',run_attempt=1)
        self.jobs=[dict(name='publish',conclusion='failure',steps=[
            dict(name='Publish native packages first and main package last',conclusion='success'),
            dict(name='Sign original archive promotion provenance',conclusion='failure')])]
        self.log='InternalError: error fetching tlog entry - (404) Not Found'

    def test_classified_failure_retries_failed_job_only(self):
        self.assertEqual(recovery_plan(self.run,self.jobs,self.log,self.sha)['retry'],'failed-jobs-only')

    def test_signed_log_redirect_cannot_forward_credentials_or_downgrade_https(self):
        request=urllib.request.Request('https://api.github.com/logs',headers={'Authorization':'Bearer fixture','Cookie':'fixture'})
        redirected=NoCredentialRedirect().redirect_request(request,None,302,'Found',{},'https://logs.example/temporary-signed-log')
        self.assertIsNone(redirected.get_header('Authorization'))
        self.assertIsNone(redirected.get_header('Cookie'))
        with self.assertRaises(ValueError):NoCredentialRedirect().redirect_request(request,None,302,'Found',{},'http://logs.example/log')

    def test_untrusted_changed_or_exhausted_runs_are_rejected(self):
        for key,value in [('repository',dict(full_name='attacker/repo')),('head_sha','b'*40),('head_branch','dev'),
                ('event','pull_request'),('path','other.yml'),('run_attempt',3),('conclusion','success')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                recovery_plan(dict(self.run,**{key:value}),self.jobs,self.log,self.sha)

    def test_upload_identity_permission_signature_and_unknown_failures_are_rejected(self):
        for log in ['403 Forbidden','signature invalid','404 missing artifact','unrecognized network failure']:
            with self.assertRaises(ValueError):recovery_plan(self.run,self.jobs,log,self.sha)
        jobs=copy.deepcopy(self.jobs);jobs[0]['steps'][0]['conclusion']='failure'
        with self.assertRaises(ValueError):recovery_plan(self.run,jobs,self.log,self.sha)


if __name__=='__main__':unittest.main()
