import datetime as dt
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
    def test_young(self):
        x=fixture(); x['queued_runs'][0]['created_at']=NOW.isoformat()
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
        with patch.object(t,'urlopen',side_effect=fake): t.api('/repos/Zennay/zCloud/actions/runners', 'x')
        self.assertEqual(calls,['GET'])
if __name__ == '__main__': unittest.main()
