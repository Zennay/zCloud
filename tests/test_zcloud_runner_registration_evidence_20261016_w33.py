"""Offline-only adversarial runner registration classification tests."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'zcloud_runner_registration_evidence_20261016_w33.py'
spec = importlib.util.spec_from_file_location('runner_registration_evidence', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


def fixture(*, state='online', busy=False, labels=('self-hosted','zcloud','vps'), name='zcloud-vps-1'):
    return {'repository':'Zennay/zCloud', 'request_url':mod.API_URL, 'method':'GET', 'http_status':200, 'captured_at':'2026-10-16T17:59:30Z',
            'response': {'total_count':1, 'runners':[{'id':21, 'name':name, 'status':state, 'busy':busy,
                'labels':[{'name':label,'type':'custom'} for label in labels]}]}}


class RunnerEvidenceTests(unittest.TestCase):
    def classify(self, value):
        report = mod.inspect(value, now=NOW)
        for field in ('independent_registration_verified','runner_group_access_verified',
                      'live_runner_health_verified','service_idle_verified','production_deploy_verified',
                      'restart_authorized','dispatch_authorized','mutation_performed'):
            self.assertIs(report[field], False)
        self.assertNotIn('id', report)
        return report['classification']

    def denies(self, value, reason):
        with self.assertRaises(mod.InvalidEvidence) as err:
            mod.inspect(value, now=NOW)
        self.assertEqual(str(err.exception), reason)

    def test_online_idle_is_only_claim(self):
        self.assertEqual(self.classify(fixture()), 'RUNNER_LISTED_ONLINE_IDLE')

    def test_busy_preserves_active_worker(self):
        self.assertEqual(self.classify(fixture(busy=True)), 'RUNNER_LISTED_BUSY')

    def test_offline_requires_independent_inspection(self):
        self.assertEqual(self.classify(fixture(state='offline')), 'RUNNER_LISTED_OFFLINE')

    def test_no_runner_is_not_listener_outage_proof(self):
        self.assertEqual(self.classify(fixture(name='another')), 'RUNNER_NOT_LISTED')

    def test_empty_complete_inventory(self):
        x = fixture(); x['response']={'total_count':0,'runners':[]}
        self.assertEqual(self.classify(x),'RUNNER_NOT_LISTED')

    def test_missing_required_label(self):
        self.assertEqual(self.classify(fixture(labels=('self-hosted','zcloud'))), 'LABEL_MISMATCH')

    def test_case_insensitive_labels(self):
        self.assertEqual(self.classify(fixture(labels=('SELF-HOSTED','ZCLOUD','VPS'))), 'RUNNER_LISTED_ONLINE_IDLE')

    def test_incomplete_page_denied(self):
        x = fixture(); x['response']['total_count']=2
        self.denies(x,'incomplete_runner_inventory')

    def test_unpaged_inventory_denied(self):
        x=fixture(); x['response']['total_count']=101
        self.denies(x,'incomplete_runner_inventory')

    def test_duplicate_runner_ids_denied(self):
        x=fixture(); x['response']['runners'].append(dict(x['response']['runners'][0],name='other')); x['response']['total_count']=2
        self.denies(x,'ambiguous_runner_identity')

    def test_duplicate_runner_names_denied(self):
        x=fixture(); x['response']['runners'].append(dict(x['response']['runners'][0],id=22)); x['response']['total_count']=2
        self.denies(x,'ambiguous_runner_identity')

    def test_bool_runner_id_denied(self):
        x=fixture(); x['response']['runners'][0]['id']=True
        self.denies(x,'invalid_runner_identity')

    def test_string_busy_denied(self):
        x=fixture(); x['response']['runners'][0]['busy']='false'
        self.denies(x,'invalid_runner_state')

    def test_unknown_status_denied(self):
        self.denies(fixture(state='starting'),'invalid_runner_state')

    def test_duplicate_casefold_labels_denied(self):
        self.denies(fixture(labels=('self-hosted','ZCloud','zcloud','vps')),'duplicate_runner_label')

    def test_missing_labels_denied(self):
        x=fixture(); del x['response']['runners'][0]['labels']
        self.denies(x,'invalid_runner_labels')

    def test_cross_repository_api_origin_denied(self):
        x = fixture(); x['request_url'] = 'https://api.github.com/repos/Zennay/Ftmo/actions/runners?per_page=100'
        self.denies(x, 'unbound_api_response')

    def test_altered_path_and_query_denied(self):
        for path in ('https://api.github.com/repos/Zennay/zCloud/actions/runners?per_page=1',
                     'https://evil.example/repos/Zennay/zCloud/actions/runners?per_page=100',
                     mod.API_URL + '&page=2'):
            with self.subTest(path=path):
                x = fixture(); x['request_url'] = path
                self.denies(x, 'unbound_api_response')

    def test_missing_origin_denied(self):
        x = fixture(); del x['request_url']
        self.denies(x, 'unbound_api_response')

    def test_non_get_http_provenance_denied(self):
        x = fixture(); x['method'] = 'POST'
        self.denies(x, 'unbound_api_response')

    def test_http_error_or_boolean_status_denied(self):
        for status in (403, 500, True, '200', None):
            with self.subTest(status=status):
                x = fixture(); x['http_status'] = status
                self.denies(x, 'unbound_api_response')

    def test_foreign_repo_denied(self):
        x=fixture(); x['repository']='Zennay/Ftmo'
        self.denies(x,'unbound_repository')

    def test_stale_denied(self):
        x=fixture(); x['captured_at']='2026-10-16T17:54:59Z'
        self.denies(x,'stale_observation')

    def test_future_clock_denied(self):
        x=fixture(); x['captured_at']='2026-10-16T18:00:16Z'
        self.denies(x,'future_observation')

    def test_future_tolerance(self):
        x=fixture(); x['captured_at']='2026-10-16T18:00:15Z'
        self.assertEqual(self.classify(x),'RUNNER_LISTED_ONLINE_IDLE')

    def test_naive_clock_denied(self):
        x=fixture(); x['captured_at']='2026-10-16T18:00:00'
        self.denies(x,'bad_observation_time')

    def test_duplicate_json_keys_denied(self):
        with self.assertRaisesRegex(mod.InvalidEvidence,'duplicate_json_key'):
            mod.load_json(b'{"repository":"Zennay/zCloud","repository":"Zennay/Ftmo"}')

    def test_nonfinite_json_denied(self):
        with self.assertRaisesRegex(mod.InvalidEvidence,'nonfinite_number'):
            mod.load_json(b'{"x":NaN}')

    def test_unpaired_unicode_surrogate_denied(self):
        with self.assertRaisesRegex(mod.InvalidEvidence,'malformed_json'):
            mod.load_json(b'{"x":"\\ud800"}')

    def test_nonfinite_float_overflow_denied(self):
        for value in (b'{"x":1e999}', b'{"x":-1e999}'):
            with self.subTest(value=value), self.assertRaisesRegex(mod.InvalidEvidence,'nonfinite_number'):
                mod.load_json(value)

    def test_oversized_input_denied(self):
        with self.assertRaisesRegex(mod.InvalidEvidence,'invalid_input_size'):
            mod.load_json(b' ' * (mod.MAX_INPUT + 1))

    def test_cli_rejects_invalid_input_without_leaking_payload(self):
        p=subprocess.run([sys.executable,str(SCRIPT)],input=b'{"secret":"hello"}',capture_output=True,check=False)
        self.assertEqual(p.returncode,2)
        self.assertIn(b'EVIDENCE_UNAVAILABLE',p.stdout)
        self.assertNotIn(b'hello',p.stdout+p.stderr)

    def test_cli_has_no_network_or_process_access(self):
        source=SCRIPT.read_text()
        for forbidden in ('urllib.request','requests.', 'subprocess.', 'os.system', 'systemctl', 'sudo ', 'GH_TOKEN', 'GITHUB_TOKEN'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
