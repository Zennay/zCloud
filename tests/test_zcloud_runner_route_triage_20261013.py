import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

F = Path(__file__).resolve().parents[1] / 'scripts/zcloud_runner_route_triage_20261013.py'
spec = importlib.util.spec_from_file_location('triage', F)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
NOW = dt.datetime(2026, 10, 13, 19, 30, tzinfo=dt.timezone.utc)

def fixture(status='online', busy=False, names=('self-hosted', 'zcloud', 'vps')):
    return {'repository': t.REPO, 'captured_at': NOW.isoformat(),
            'runners': [{'id': 21, 'status': status, 'busy': busy, 'labels': [{'name': n} for n in names]}],
            'queued_runs': [{'id': 123, 'status': 'queued', 'created_at': (NOW - dt.timedelta(minutes=8)).isoformat()}],
            'jobs_by_run': {'123': [{'id': 456, 'status': 'queued', 'runner_id': 0,
                                     'created_at': (NOW - dt.timedelta(minutes=8)).isoformat(),
                                     'labels': ['self-hosted', 'zcloud', 'vps']}]}}

class TestRunnerRoute(unittest.TestCase):
    def classify(self, x): return t.classify(x, NOW)
    def test_available_not_progress(self):
        result = self.classify(fixture())
        self.assertEqual(result['classification'], 'route_available_but_queued')
        self.assertFalse(result['recovery_authorized'])
        self.assertFalse(result['generation_verified'])
    def test_offline(self): self.assertEqual(self.classify(fixture(status='offline'))['classification'], 'runner_offline')
    def test_busy(self): self.assertEqual(self.classify(fixture(busy=True))['classification'], 'runner_busy')
    def test_wrong_labels(self): self.assertEqual(self.classify(fixture(names=('self-hosted','ftmo-research')))['classification'], 'labels_or_access_unproven')
    def test_group_not_assumed_routable(self):
        x=fixture(); x['jobs_by_run']['123'][0]['runner_group_id']=101
        result=self.classify(x)
        self.assertEqual(result['classification'], 'labels_or_access_unproven')
        self.assertEqual(result['by_cause']['group_access_unverified'], 1)
    def test_invalid_group_id(self):
        x=fixture(); x['jobs_by_run']['123'][0]['runner_group_id']=True
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_in_progress_workflow_with_queued_deploy_job(self):
        x=fixture()
        x['queued_runs'][0]['status']='in_progress'
        result=self.classify(x)
        self.assertEqual(result['stale_unassigned_jobs'], 1)
        self.assertEqual(result['classification'], 'route_available_but_queued')
    def test_waiting_workflow_with_queued_job(self):
        x=fixture()
        x['queued_runs'][0]['status']='waiting'
        self.assertEqual(self.classify(x)['stale_unassigned_jobs'], 1)
    def test_unexpected_completed_workflow_denied(self):
        x=fixture()
        x['queued_runs'][0]['status']='completed'
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_live_gathers_in_progress_as_well_as_queued(self):
        calls=[]
        def fake(path, field, token, limit=500):
            calls.append((path,field))
            if field == 'runners': return fixture()['runners']
            if field == 'workflow_runs':
                status=path.split('status=')[1]
                return [{'id': 100 + len(calls), 'status': status,
                         'created_at': (NOW-dt.timedelta(minutes=10)).isoformat()}]
            return [{'id': len(calls)+200, 'status': 'queued', 'labels': ['self-hosted'], 'runner_id': 0}]
        with patch.object(t, 'pages', side_effect=fake):
            snap=t.live('token')
        self.assertIn('status=in_progress', ' '.join(path for path,_ in calls))
        self.assertIn('status=queued', ' '.join(path for path,_ in calls))
        self.assertEqual(len(snap['queued_runs']), len([x for x in calls if x[1]=='workflow_runs']))
    def test_old_run_but_new_queued_job_is_not_stale(self):
        x=fixture()
        x['queued_runs'][0]['created_at']=(NOW-dt.timedelta(hours=3)).isoformat()
        x['jobs_by_run']['123'][0]['created_at']=(NOW-dt.timedelta(seconds=30)).isoformat()
        self.assertEqual(self.classify(x)['stale_unassigned_jobs'], 0)
    def test_queued_job_missing_created_at_is_not_proven_idle(self):
        x=fixture(); x['jobs_by_run']['123'][0].pop('created_at')
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_queued_job_created_before_workflow_is_invalid(self):
        x=fixture(); x['jobs_by_run']['123'][0]['created_at']=(NOW-dt.timedelta(hours=1)).isoformat()
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_queued_job_future_clock_is_invalid(self):
        x=fixture(); x['jobs_by_run']['123'][0]['created_at']=(NOW+dt.timedelta(minutes=10)).isoformat()
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_malformed_api_content_type_refused(self):
        class Resp:
            status=200; headers={'content-type':'application/jsonp'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b'{}'
        class Opener:
            def open(self, req, timeout): return Resp()
        with patch.object(t,'build_opener',return_value=Opener()):
            with self.assertRaises(t.EvidenceError): t.api('/repos/Zennay/zCloud/actions/runners','secret')
    def test_future_run_clock(self):
        x=fixture(); x['queued_runs'][0]['created_at']=(NOW+dt.timedelta(hours=1)).isoformat()
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_young(self):
        x=fixture(); x['jobs_by_run']['123'][0]['created_at']=NOW.isoformat()
        self.assertEqual(self.classify(x)['classification'], 'no_stale_unassigned_jobs')
    def test_assigned(self):
        x=fixture(); x['jobs_by_run']['123'][0]['runner_id']=21
        self.assertEqual(self.classify(x)['classification'], 'no_stale_unassigned_jobs')
    def test_missing_runner(self):
        x=fixture(); del x['runners']
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_missing_jobs(self):
        x=fixture(); x['jobs_by_run']={}
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_duplicate_runs(self):
        x=fixture(); x['queued_runs'].append(x['queued_runs'][0])
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_duplicate_runner(self):
        x=fixture(); x['runners'].append(x['runners'][0])
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_future_snapshot(self):
        x=fixture(); x['captured_at']=(NOW+dt.timedelta(hours=1)).isoformat()
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_wrong_repo(self):
        x=fixture(); x['repository']='Zennay/Ftmo'
        with self.assertRaises(t.EvidenceError): self.classify(x)
    def test_duplicate_json(self):
        with self.assertRaises(t.EvidenceError): t.strict_json('{"x":1,"x":2}')
    def test_nonfinite_json(self):
        with self.assertRaises(t.EvidenceError): t.strict_json('{"x":NaN}')
    def test_surrogate_json(self):
        with self.assertRaises(t.EvidenceError): t.strict_json('{"x":"\\ud800"}')
    def test_bounded_pages(self):
        with patch.object(t, 'api', return_value={'jobs': [{}]*100, 'total_count':900}):
            with self.assertRaises(t.EvidenceError): t.pages('/repos/Zennay/zCloud/actions/runs/1/jobs', 'jobs', 'x')
    def test_incomplete_count(self):
        with patch.object(t, 'api', return_value={'runners': [], 'total_count':1}):
            with self.assertRaises(t.EvidenceError): t.pages('/repos/Zennay/zCloud/actions/runners','runners','x')
    def test_api_forbidden_endpoint(self):
        with self.assertRaises(t.EvidenceError): t.api('/repos/Zennay/zCloud/issues', 'x')
    def test_missing_token(self):
        with self.assertRaises(t.EvidenceError): t.live('')
    def test_redirect_denied_before_follow(self):
        with self.assertRaises(t.EvidenceError):
            t.RefuseRedirect().redirect_request(None, None, 302, 'moved', {}, 'https://example.invalid/')
    def test_api_get_only(self):
        calls=[]
        def fake(req, timeout):
            calls.append(req.get_method())
            class Resp:
                status=200; headers={'content-type':'application/json'}
                def __enter__(self): return self
                def __exit__(self,*a): pass
                def read(self,n): return b'{"runners":[]}'
            return Resp()
        class Opener:
            def open(self, req, timeout): return fake(req, timeout)
        with patch.object(t, 'build_opener', return_value=Opener()):
            t.api('/repos/Zennay/zCloud/actions/runners', 'x')
        self.assertEqual(calls,['GET'])
class CliContract(unittest.TestCase):
    def call_fixture(self, mutate=None):
        now=dt.datetime.now(dt.timezone.utc)
        snap=fixture()
        snap['captured_at']=now.isoformat()
        snap['queued_runs'][0]['created_at']=(now-dt.timedelta(minutes=8)).isoformat()
        snap['jobs_by_run']['123'][0]['created_at']=(now-dt.timedelta(minutes=8)).isoformat()
        if mutate: mutate(snap)
        with tempfile.TemporaryDirectory() as home:
            path=Path(home)/'snapshot.json'
            path.write_text(json.dumps(snap), encoding='utf-8')
            output=io.StringIO()
            with patch.object(t, 'api', side_effect=AssertionError('fixture invoked network')), contextlib.redirect_stdout(output):
                rc=t.main(['--fixture',str(path)])
            return rc, json.loads(output.getvalue())
    def test_cli_stale_exit_1(self):
        rc, result=self.call_fixture()
        self.assertEqual(rc, 1)
        self.assertFalse(result['queue_cancel_authorized'])
    def test_cli_drained_exit_0(self):
        rc, result=self.call_fixture(lambda s: s['queued_runs'].clear())
        self.assertEqual(rc, 0)
        self.assertEqual(result['classification'], 'no_stale_unassigned_jobs')
    def test_cli_missing_inventory_exit_2(self):
        rc, result=self.call_fixture(lambda s: s.pop('runners'))
        self.assertEqual(rc, 2)
        self.assertEqual(result['classification'], 'unknown_incomplete_evidence')
    def test_cli_no_sensitive_payload_echo(self):
        def insert(s):
            s['queued_runs'][0]['name']='SECRET_TEST_RUN_NAME'
            s['jobs_by_run']['123'][0]['name']='SECRET_TEST_JOB_NAME'
        rc, result=self.call_fixture(insert)
        self.assertEqual(rc,1)
        self.assertNotIn('SECRET_TEST',json.dumps(result))
    def test_cli_live_without_token_fail_closed(self):
        output=io.StringIO()
        with patch.dict(os.environ, {'GITHUB_TOKEN': ''}), contextlib.redirect_stdout(output):
            rc=t.main(['--live'])
        self.assertEqual(rc,2)
        self.assertFalse(json.loads(output.getvalue())['deploy_authorized'])

if __name__ == '__main__': unittest.main()
