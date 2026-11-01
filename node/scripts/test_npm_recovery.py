import copy
import unittest
import urllib.request
import hashlib
import json
from pathlib import Path
import tempfile
from release_assets import verify as verify_assets
import runpy
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

    def test_resume_requires_exact_trusted_workflow_and_current_publisher(self):
        validate=runpy.run_path(str(Path(__file__).with_name('restore-publication.py')))['validate_run']
        validate(self.run,{'GITHUB_SHA':self.sha})
        for key,value in [('event','pull_request'),('head_sha','b'*40),('head_branch','dev'),('path','other.yml'),
                          ('repository',{'full_name':'other/repo'})]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate(dict(self.run,**{key:value}),{'GITHUB_SHA':self.sha})

    def test_changed_publisher_requires_verified_ancestry_and_preserves_immutable_progress(self):
        helpers=runpy.run_path(str(Path(__file__).with_name('restore-publication.py')))
        validate,rebind=helpers['validate_run'],helpers['rebind_progress']
        calls=[]
        certify=lambda old,new:calls.append((old,new))
        newer='b'*40
        validate(self.run,{'GITHUB_SHA':newer},certify)
        self.assertEqual(calls,[(self.sha,newer)])
        old=dict(revision=self.sha,publisher_revision=self.sha,version='1.0.0',publish_order=[
            dict(name='native',version='1.0.0',sha256='c'*64)])
        new=dict(old,publisher_revision=newer)
        before=json.dumps(old).encode();after=json.dumps(new).encode()
        state=dict(identity=dict(producer=self.sha,publisher=self.sha,manifest_sha256=hashlib.sha256(before).hexdigest()),
            run_id='1',attempt='2',started_at=100,deadline=480100,attempts=['1/1','1/2'],
            read_counts={'endpoint':15},items=[dict(old['publish_order'][0],state='uploaded-pending')])
        result=rebind(state,before,after,certify)
        for key in ('started_at','deadline','attempts','read_counts','items'):
            self.assertEqual(result[key],state[key])
        self.assertEqual(result['publisher_history'][0]['identity'],state['identity'])
        self.assertEqual(result['identity']['publisher'],newer)
        self.assertEqual(state['identity']['publisher'],self.sha)
        for altered in [dict(new,revision='d'*40),dict(new,version='2.0.0'),
                dict(new,publish_order=[dict(new['publish_order'][0],sha256='e'*64)])]:
            with self.assertRaises(ValueError):rebind(state,before,json.dumps(altered).encode(),certify)
        with self.assertRaises(ValueError):rebind(dict(state,identity={}),before,after,certify)
        def reject(*args):raise ValueError('runtime changed')
        with self.assertRaises(ValueError):rebind(state,before,after,reject)

    def test_structured_pending_confirmation_preserves_deadline_and_rejects_other_failures(self):
        jobs=copy.deepcopy(self.jobs)
        jobs[0]['steps'][0]['conclusion']='failure'
        jobs[0]['steps'][1]['conclusion']='skipped'
        progress=dict(schema=2,status='pending-confirmation',error={'code':'registry-readback-pending'},
            run_id=1,attempt=1,identity={'publisher':self.sha},started_at=100,deadline=480100,attempts=['1/1'],
            items=[dict(name='native'+str(i),state='uploaded-pending' if i==0 else 'new') for i in range(6)])
        result=recovery_plan(self.run,jobs,'readback budget',self.sha,progress,now=200)
        self.assertEqual(result['deadline'],480100)
        for changed in [dict(progress,deadline=999999),dict(progress,status='failure'),
                        dict(progress,identity={'publisher':'b'*40}),dict(progress,attempts=['1/1','1/2','1/3'])]:
            with self.assertRaises(ValueError):recovery_plan(self.run,jobs,'',self.sha,changed,now=200)
        with self.assertRaises(ValueError):recovery_plan(self.run,jobs,'',self.sha,progress,now=480100)

    def test_release_assets_reject_unsigned_progress_extra_files_and_wrong_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            names=[f'package-{i}.tgz' for i in range(6)]
            for name in names+['RELEASE_NOTES.md','SHA256SUMS','provenance.sigstore.json']:
                (root/name).write_bytes(b'fixture')
            (root/'npm-release-manifest.json').write_text(json.dumps({'publish_order':[{'file':n} for n in names]}))
            assets=[{'name':p.name,'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.iterdir()]
            verify_assets(root,assets)
            with self.assertRaises(ValueError):verify_assets(root,assets+[{'name':'publication.json'}])
            with self.assertRaises(ValueError):verify_assets(root,assets[:-1])
            assets[0]['digest']='sha256:'+'0'*64
            with self.assertRaises(ValueError):verify_assets(root,assets)

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
