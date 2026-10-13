"""Real localhost HTTP + GitHub-shaped API acceptance, zero production requests."""
import contextlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/zcloud_runner_route_triage_20261013.py'
spec = importlib.util.spec_from_file_location('zcloud_route_http', SCRIPT)
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # The fixture must never log Authorization or private job text.

    def do_GET(self):
        owner = self.server.owner
        p = urlparse(self.path)
        owner.calls.append((self.command, p.path, parse_qs(p.query), self.headers.get('Authorization')))
        base = '/repos/Zennay/zCloud/actions'
        if p.path == base + '/runners':
            owner.runner_reads += 1
            if owner.mode == 'runner_forbidden':
                self.reply(b'{"message":"Resource not accessible"}', code=403)
                return
            if owner.mode == 'runner_redirect':
                self.send_response(302)
                self.send_header('Location', 'http://127.0.0.1:%d/redirect-leak' % self.server.server_port)
                self.end_headers()
                return
            if owner.mode == 'runner_oversized':
                self.reply(b' ' * (route.MAX_BODY + 1))
                return
            if owner.mode == 'runner_duplicate_key':
                self.reply(b'{"runners":[],"runners":[]}')
                return
            if owner.mode == 'runner_invalid_mime':
                self.reply(b'{"runners":[]}', mime='text/plain')
                return
            if owner.mode == 'runner_sneaky_nan':
                self.reply(b'{"runners":[],"unused":1e999}')
                return
            runners = owner.runners()
            page = int(parse_qs(p.query).get('page', ['1'])[0])
            if owner.mode == 'runner_changed' and owner.runner_reads > 1:
                runners[0]['busy'] = True
            self.reply_json({'total_count':len(runners),
                             'runners':runners[(page-1)*100:page*100]})
            return
        if p.path == base + '/runs':
            state = parse_qs(p.query).get('status',[''])[0]
            if owner.mode == 'too_many_runs' and state == 'queued':
                runs = [{'id':i + 1, 'status':'queued','created_at':owner.run_time}
                        for i in range(41)]
            else:
                runs = [{'id':123,'status':'in_progress','created_at':owner.run_time}] if state == 'in_progress' else []
            page = int(parse_qs(p.query).get('page',['1'])[0])
            self.reply_json({'total_count':len(runs),
                             'workflow_runs':runs[(page-1)*100:page*100]})
            return
        if p.path == base + '/runs/123/jobs':
            if owner.mode == 'jobs_forbidden':
                self.reply(b'{"message":"forbidden"}', code=403)
                return
            page = int(parse_qs(p.query).get('page',['1'])[0])
            jobs = [{'id':456,'status':'queued', 'created_at': owner.job_time,
                     'labels':['self-hosted','zcloud','vps'], 'runner_id':0,
                     'name':'SECRET_TEST_JOB_NAME'}]
            self.reply_json({'total_count':len(jobs),'jobs':jobs[(page-1)*100:page*100]})
            return
        self.reply(b'{"error":"not found"}', code=404)

    def reply_json(self,obj):
        self.reply(json.dumps(obj).encode('utf-8'))

    def reply(self, data, code=200, mime='application/json'):
        self.send_response(code)
        self.send_header('Content-Type',mime)
        self.end_headers()
        self.wfile.write(data)

class RealHttpTests(unittest.TestCase):
    def setUp(self):
        self.mode = 'ok'
        self.calls = []
        self.runner_reads = 0
        self.runner_count = 1
        now = datetime.now(timezone.utc)
        self.run_time = (now-timedelta(minutes=10)).isoformat()
        self.job_time = (now-timedelta(minutes=5)).isoformat()
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.owner=self
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.patch_api = patch.object(route,'API_BASE',
                                      'http://127.0.0.1:%d' % self.server.server_port)
        self.patch_api.start()

    def tearDown(self):
        self.patch_api.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def runners(self):
        return [{'id':i+1,'status':'online','busy':False,
                 'labels':[{'name':j} for j in ('self-hosted','zcloud','vps')]}
                for i in range(self.runner_count)]

    def live(self):
        return route.live('PRIVATE_SENTINEL_TOKEN')

    def test_real_http_in_progress_run_and_queued_vps_job(self):
        snap=self.live()
        result=route.classify(snap,datetime.now(timezone.utc))
        self.assertEqual(result['stale_unassigned_jobs'],1)
        self.assertEqual(result['classification'],'route_available_but_queued')
        self.assertFalse(result['recovery_authorized'])
        self.assertEqual(self.runner_reads,2)
        self.assertEqual(len([c for c in self.calls if '/runs/123/jobs' in c[1]]),1)
        self.assertTrue(all(c[0]=='GET' for c in self.calls))
        self.assertTrue(all(c[3]=='Bearer PRIVATE_SENTINEL_TOKEN' for c in self.calls))

    def test_real_http_two_page_runner_inventory(self):
        self.runner_count=101
        result=self.live()
        self.assertEqual(len(result['runners']),101)
        self.assertEqual(self.runner_reads,4) # page 1, page 2, then both again

    def test_real_http_runner_busy_race_denied(self):
        self.mode='runner_changed'
        with self.assertRaisesRegex(route.EvidenceError,'roster_changed'):
            self.live()

    def test_real_http_runner_permission_denial_is_unknown(self):
        self.mode='runner_forbidden'
        with self.assertRaisesRegex(route.EvidenceError,'api_unavailable'):
            self.live()

    def test_real_http_job_permission_denial_is_unknown(self):
        self.mode='jobs_forbidden'
        with self.assertRaisesRegex(route.EvidenceError,'api_unavailable'):
            self.live()

    def test_real_http_redirect_rejected_before_leaking_auth(self):
        self.mode='runner_redirect'
        with self.assertRaisesRegex(route.EvidenceError,'redirect_denied'):
            self.live()
        self.assertFalse(any(c[1]=='/redirect-leak' for c in self.calls))

    def test_real_http_duplicate_json_refused(self):
        self.mode='runner_duplicate_key'
        with self.assertRaisesRegex(route.EvidenceError,'duplicate_json_key'):
            self.live()

    def test_real_http_oversized_json_refused(self):
        self.mode='runner_oversized'
        with self.assertRaisesRegex(route.EvidenceError,'oversized_response'):
            self.live()

    def test_real_http_bad_mime_refused(self):
        self.mode='runner_invalid_mime'
        with self.assertRaisesRegex(route.EvidenceError,'untrusted_api_response'):
            self.live()

    def test_real_http_json_exponent_overflow_denied(self):
        self.mode='runner_sneaky_nan'
        with self.assertRaisesRegex(route.EvidenceError,'nonfinite_json'):
            self.live()

    def test_real_http_inventory_more_than_40_denied(self):
        self.mode='too_many_runs'
        with self.assertRaisesRegex(route.EvidenceError,'incomplete_inventory'):
            self.live()

    def test_real_http_cli_output_redacted_and_not_authorizing(self):
        output=io.StringIO()
        with patch.dict(os.environ,{'GITHUB_TOKEN':'PRIVATE_SENTINEL_TOKEN'}):
            with contextlib.redirect_stdout(output):
                rc=route.main(['--live'])
        result=json.loads(output.getvalue())
        self.assertEqual(rc,1)
        self.assertEqual(result['classification'],'route_available_but_queued')
        self.assertFalse(result['deploy_authorized'])
        self.assertNotIn('PRIVATE_SENTINEL_TOKEN',output.getvalue())
        self.assertNotIn('SECRET_TEST_JOB_NAME',output.getvalue())

if __name__=='__main__':
    unittest.main()
