"""Offline contract for Github-hosted read-only queue receipts."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import zcloud_runner_route_receipt_20261013 as r


def item(kind='unknown_incomplete_evidence', stale=7, unknown=5):
    return {
        'classification': kind,
        'stale_unassigned_jobs': stale,
        'unknown_stale_queued_runs': unknown,
        'stale_label_profiles': {'ftmo_research': stale, 'zcloud_vps': 0, 'other': 0},
        'runner_inventory_verified': False,
        'evidence_mode': 'github_api_jobs_only',
        'recovery_authorized': False,
        'queue_cancel_authorized': False,
        'deploy_authorized': False,
        'generation_verified': False,
        'job_name': 'SECRET_TEST_JOB_NAME',
        'token': 'SECRET_TEST_TOKEN',
    }


class TestReceipt(unittest.TestCase):
    def parse(self, data, exit_code):
        return r.receipt(json.dumps(data).encode(), exit_code)

    def test_mixed_partial_unknown_receipt(self):
        x = self.parse(item(), 2)
        self.assertEqual(x['stale_unassigned_jobs'], 7)
        self.assertEqual(x['unknown_stale_queued_runs'], 5)
        self.assertFalse(x['mutation_authorized'])
        self.assertFalse(x['production_status_verified'])
        self.assertNotIn('SECRET_TEST', json.dumps(x))

    def test_no_jobs_green_but_not_production_verified(self):
        x = self.parse(item('no_stale_unassigned_jobs', 0, 0), 0)
        self.assertEqual(x['probe_exit'], 0)
        self.assertFalse(x['runner_inventory_verified'])
        self.assertFalse(x['production_status_verified'])

    def test_known_stale_jobs_exit_one(self):
        x = self.parse(item('labels_or_access_unproven', 7, 0), 1)
        self.assertEqual(x['stale_unassigned_jobs'], 7)

    def test_api_failure_without_partial_counts_is_unknown(self):
        x = self.parse({'classification': 'unknown_incomplete_evidence',
                        'recovery_authorized': False,
                        'queue_cancel_authorized': False,
                        'deploy_authorized': False}, 2)
        self.assertEqual(x['stale_unassigned_jobs'], 0)
        self.assertEqual(x['unknown_stale_queued_runs'], 0)
        self.assertFalse(x['runner_inventory_verified'])

    def test_denies_mutation_authority(self):
        for k in r.DENIED:
            x = item()
            x[k] = True
            with self.subTest(k=k), self.assertRaises(r.EvidenceError):
                self.parse(x, 2)

    def test_sanitized_profile_counts_are_pinned(self):
        got = self.parse(item(), 2)
        self.assertEqual(got['stale_label_profiles']['ftmo_research'], 7)
        self.assertEqual(got['stale_label_profiles']['zcloud_vps'], 0)

    def test_rejects_histogram_overcount(self):
        x = item()
        x['stale_label_profiles']['ftmo_research'] = 8
        with self.assertRaisesRegex(r.EvidenceError,'contradictory_label_profile_count'):
            self.parse(x, 2)

    def test_rejects_unexpected_label_in_receipt(self):
        x = item()
        x['stale_label_profiles']['private-runner-token'] = 1
        with self.assertRaisesRegex(r.EvidenceError,'invalid_label_profile_shape'):
            self.parse(x, 2)

    def test_rejects_bool_in_profile(self):
        x = item()
        x['stale_label_profiles']['ftmo_research'] = True
        with self.assertRaisesRegex(r.EvidenceError,'invalid_count'):
            self.parse(x, 2)

    def test_denies_generation_claim(self):
        x = item(); x['generation_verified'] = True
        with self.assertRaises(r.EvidenceError):
            self.parse(x, 2)

    def test_denies_false_green_count(self):
        x = item('no_stale_unassigned_jobs', 1, 0)
        with self.assertRaises(r.EvidenceError):
            self.parse(x, 0)

    def test_denies_unproven_stale_without_count(self):
        x = item('labels_or_access_unproven', 0, 0)
        with self.assertRaises(r.EvidenceError):
            self.parse(x, 1)

    def test_denies_conflicting_exit_code(self):
        with self.assertRaises(r.EvidenceError):
            self.parse(item(), 0)

    def test_denies_untrusted_roster_claim(self):
        x = item(); x['runner_inventory_verified'] = True
        with self.assertRaises(r.EvidenceError):
            self.parse(x, 2)

    def test_denies_offline_fixture_accepted_as_live(self):
        x = item('labels_or_access_unproven', 7, 0)
        x['evidence_mode'] = 'offline_fixture'
        with self.assertRaises(r.EvidenceError):
            self.parse(x, 1)

    def test_denies_boolean_count(self):
        x = item(); x['unknown_stale_queued_runs'] = True
        with self.assertRaises(r.EvidenceError):
            self.parse(x, 2)

    def test_denies_json_duplicates(self):
        with self.assertRaises(r.EvidenceError):
            r.receipt(b'{"classification":"unknown_incomplete_evidence","classification":"no_stale_unassigned_jobs"}', 2)

    def test_main_invalid_token_never_echoes(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'probe-output.json'
            x = item(); x['deploy_authorized'] = True
            f.write_text(json.dumps(x))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = r.main([str(f), '2'])
        self.assertEqual(rc, 2)
        self.assertNotIn('SECRET_TEST', out.getvalue())
        self.assertFalse(json.loads(out.getvalue())['mutation_authorized'])

    def test_main_valid_partial_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'probe-output.json'
            f.write_text(json.dumps(item()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = r.main([str(f), '2'])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())['stale_unassigned_jobs'], 7)

    def test_main_oversized_input_denied(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'oversized.json'
            f.write_bytes(b'Z' * (r.MAX_BODY + 1))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = r.main([str(f), '2'])
        self.assertEqual(rc, 2)
        self.assertNotIn('Z' * 20, out.getvalue())

if __name__ == '__main__':
    unittest.main()
