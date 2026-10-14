"""Deterministic deny-first PR/PR-less ownership admission regressions."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts/zcloud_deployops_review_candidates_20261014_w1.py'
spec = importlib.util.spec_from_file_location('candidate_policy', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
S = 'a' * 40
H = 'b' * 40

def payload():
    return {'main_sha': S, 'inventory': {'prs_complete': True, 'branches_complete': True},
            'gates': {'580': 'open', '1089': 'open', '1135': 'open'},
            'prs': [{'number': 12, 'head_sha': H, 'base_sha': S,
                     'draft': False, 'owner_approved': True, 'files': ['scripts/a.py'], 'required_checks': ['tests'],
                     'checks': [{'name': 'tests', 'head_sha': H, 'conclusion': 'success'}]}],
            'branches': []}

class ReviewQueueTests(unittest.TestCase):
    def assert_denied(self, mutation):
        data = payload()
        mutation(data)
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(data)

    def test_advisory_only(self):
        report = mod.snapshot(payload())
        self.assertEqual(report['review_candidate_count'], 1)
        self.assertFalse(report['release_authorized'])
        self.assertFalse(report['mutations_performed'])

    def test_closed_gates_still_cannot_authorize(self):
        data = payload()
        data['gates'] = dict.fromkeys(data['gates'], 'closed')
        self.assertTrue(mod.snapshot(data)['serialized_gates_closed'])
        self.assertFalse(mod.snapshot(data)['release_authorized'])

    def test_stale_base_denied(self):
        d = payload(); d['prs'][0]['base_sha'] = H
        self.assertEqual(mod.snapshot(d)['review_candidates'][0]['reasons'], ['stale_base'])

    def test_draft_owner_unapproved(self):
        d = payload(); d['prs'][0]['draft'] = True; d['prs'][0]['owner_approved'] = False
        self.assertEqual(mod.snapshot(d)['review_candidate_count'], 0)
        self.assertIn('draft', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_old_head_green_denied(self):
        d = payload(); d['prs'][0]['checks'][0]['head_sha'] = S
        self.assertIn('exact_head_checks_not_green', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_failed_or_skipped_checks_denied(self):
        for state in ('failure', 'pending', 'cancelled', 'skipped', 'neutral'):
            d = payload(); d['prs'][0]['checks'][0]['conclusion'] = state
            self.assertEqual(mod.snapshot(d)['review_candidate_count'], 0)

    def test_open_draft_pr_still_reserves_files(self):
        d = payload(); other = copy.deepcopy(d['prs'][0]); other['number'] = 13
        other['draft'] = True; d['prs'].append(other)
        self.assertIn('overlapping_owner', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_prless_branch_collision_denied(self):
        d = payload(); d['branches'] = [{'name': 'owner-active', 'head_sha': H, 'files': ['scripts/a.py']}]
        self.assertEqual(mod.snapshot(d)['review_candidate_count'], 0)

    def test_disjoint_branch_not_blocking(self):
        d = payload(); d['branches'] = [{'name': 'owner-active', 'head_sha': H, 'files': ['scripts/b.py']}]
        self.assertEqual(mod.snapshot(d)['review_candidate_count'], 1)

    def test_incomplete_inventory_denied(self):
        self.assert_denied(lambda d: d['inventory'].update(branches_complete=False))

    def test_unknown_gate_denied(self):
        self.assert_denied(lambda d: d['gates'].update({'1089': 'unknown'}))

    def test_duplicate_pr_denied(self):
        self.assert_denied(lambda d: d['prs'].append(copy.deepcopy(d['prs'][0])))

    def test_duplicate_check_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['checks'].append(copy.deepcopy(d['prs'][0]['checks'][0])))

    def test_invalid_json_duplicate_key(self):
        with self.assertRaises(mod.InvalidEvidence):
            mod.unique_pairs('{"x":1,"x":2}')

    def test_invalid_json_nonfinite(self):
        with self.assertRaises(mod.InvalidEvidence):
            mod.unique_pairs('{"x": NaN}')

    def test_path_traversal_denied(self):
        for bad in ('../x', 'x/../y', '/tmp/x', 'x//y', 'x\\y', 'x/./y', 'x\u0000'):
            self.assert_denied(lambda d: d['prs'][0].update(files=[bad]))

    def test_invalid_boolean_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(draft=1))

    def test_bad_shas_denied(self):
        self.assert_denied(lambda d: d.update(main_sha='f'*39))

    def test_missing_required_green_check_denied(self):
        d = payload(); d['prs'][0]['required_checks'] = ['security', 'tests']
        self.assertIn('required_checks_missing', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_empty_required_check_policy_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(required_checks=[]))

    def test_duplicate_required_check_policy_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(required_checks=['tests', 'tests']))

    def test_empty_checks_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(checks=[]))

    def test_cli_fail_closed_does_not_leak_snapshot(self):
        with tempfile.TemporaryDirectory() as t:
            path = pathlib.Path(t) / 'secret-token-manifest.json'
            path.write_text('{"token":"do-not-print","token":"do-not-print"}')
            r = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn('do-not-print', r.stdout+r.stderr)
            self.assertFalse(json.loads(r.stdout)['release_authorized'])

    def test_cli_valid(self):
        with tempfile.TemporaryDirectory() as t:
            path = pathlib.Path(t) / 'input.json'
            path.write_text(json.dumps(payload()))
            r = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)['review_candidate_count'], 1)

if __name__ == '__main__':
    unittest.main()
