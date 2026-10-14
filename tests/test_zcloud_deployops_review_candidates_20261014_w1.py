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
                     'draft': False, 'owner_approved': True, 'files': ['scripts/a.py'], 'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}, 'required_checks': ['tests'],
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
        d = payload(); d['branches'] = [{'name': 'owner-active', 'head_sha': H, 'files': ['scripts/a.py'], 'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}}]
        self.assertEqual(mod.snapshot(d)['review_candidate_count'], 0)

    def test_disjoint_branch_not_blocking(self):
        d = payload(); d['branches'] = [{'name': 'owner-active', 'head_sha': H, 'files': ['scripts/b.py'], 'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}}]
        self.assertEqual(mod.snapshot(d)['review_candidate_count'], 1)

    def test_per_pr_incomplete_diff_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['files_evidence'].update(complete=False))

    def test_per_pr_truncated_diff_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['files_evidence'].update(total_count=301))

    def test_file_sha_mismatch_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['files_evidence'].update(head_sha=S))

    def test_file_base_drift_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['files_evidence'].update(base_sha=H))

    def test_file_count_bool_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['files_evidence'].update(total_count=True))

    def test_file_count_negative_denied(self):
        self.assert_denied(lambda d: d['prs'][0]['files_evidence'].update(total_count=-1))

    def test_file_evidence_missing_denied(self):
        self.assert_denied(lambda d: d['prs'][0].pop('files_evidence'))

    def test_unbound_branch_file_evidence_denied(self):
        d = payload()
        d['branches'] = [{'name': 'owner', 'head_sha': H, 'files': ['scripts/z.py'],
                          'files_evidence': {'complete': False, 'total_count': 1, 'head_sha': H, 'base_sha': S}}]
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_zero_change_pr_denied(self):
        d = payload()
        d['prs'][0]['files_evidence']['total_count'] = 0
        d['prs'][0]['files'] = []
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_empty_unmerged_branch_is_safe(self):
        d = payload()
        d['branches'] = [{'name': 'empty', 'head_sha': H, 'files': [],
                          'files_evidence': {'complete': True, 'total_count': 0, 'head_sha': H, 'base_sha': S}}]
        self.assertEqual(mod.snapshot(d)['review_candidate_count'], 1)

    def test_many_disjoint_owners_stay_review_candidates(self):
        d = payload()
        d['prs'] = []
        d['branches'] = []
        for i in range(1, 201):
            p = copy.deepcopy(payload()['prs'][0])
            p['number'] = i
            p['files'] = [f'feature/pr-{i}/run.py']
            d['prs'].append(p)
            d['branches'].append({
                'name': f'worker-{i}', 'head_sha': H,
                'files': [f'feature/branch-{i}/run.py'],
                'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
            })
        report = mod.snapshot(d)
        self.assertEqual(report['review_candidate_count'], 200)
        self.assertFalse(report['release_authorized'])

    def test_tail_ownership_overlap_in_large_inventory_denied(self):
        d = payload()
        d['branches'] = []
        for i in range(200):
            path = 'scripts/a.py/new-file.py' if i == 199 else f'other/file-{i}.py'
            d['branches'].append({'name': f'branch-{i}', 'head_sha': H,
                                  'files': [path],
                                  'files_evidence': {'complete': True, 'total_count': 1,
                                                     'head_sha': H, 'base_sha': S}})
        self.assertIn('overlapping_owner', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_ancestry_index_marks_both_colliding_prs(self):
        d = payload()
        other = copy.deepcopy(d['prs'][0])
        other['number'] = 13
        other['files'] = ['scripts/a.py/inner']
        d['prs'].append(other)
        report = mod.snapshot(d)
        self.assertEqual(report['review_candidate_count'], 0)
        self.assertTrue(all('overlapping_owner' in p['reasons'] for p in report['review_candidates']))

    def test_file_to_directory_swap_collides(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['scripts/a.py/subfile'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_directory_to_file_swap_collides(self):
        d = payload()
        d['prs'][0]['files'] = ['scripts/a.py/subfile']
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['scripts/a.py'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_case_only_file_collision_rejected(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['SCRIPTS/A.PY'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_nfc_normalized_file_collision_rejected(self):
        d = payload()
        d['prs'][0]['files'] = ['src/café.py']
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['src/cafe\u0301.py'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', mod.snapshot(d)['review_candidates'][0]['reasons'])

    def test_unicode_duplicate_within_pr_denied(self):
        d = payload()
        d['prs'][0]['files'] = ['src/café.py', 'src/cafe\u0301.py']
        d['prs'][0]['files_evidence']['total_count'] = 2
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_sibling_names_are_disjoint(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['scripts/a.py-next'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
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

    def test_cli_huge_integer_is_sanitized(self):
        with tempfile.TemporaryDirectory() as t:
            path = pathlib.Path(t) / 'too-large-int.json'
            path.write_text('{"secret":' + '9' * 6000 + '}')
            r = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(json.loads(r.stdout)['release_authorized'])
            self.assertNotIn('Traceback', r.stdout + r.stderr)

    def test_cli_recursive_json_is_sanitized(self):
        with tempfile.TemporaryDirectory() as t:
            path = pathlib.Path(t) / 'recursive.json'
            path.write_text('[' * 1500 + '0' + ']' * 1500)
            r = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(json.loads(r.stdout)['release_authorized'])
            self.assertNotIn('Traceback', r.stdout + r.stderr)

    def test_markdown_never_authorizes_release(self):
        d = payload(); d['gates'] = dict.fromkeys(d['gates'], 'closed')
        report = mod.markdown(mod.snapshot(d))
        self.assertIn('Geen toestemming', report)
        self.assertIn('PR #12', report)
        self.assertNotIn('scripts/a.py', report)
        self.assertNotIn('vrijgegeven', report)

    def test_markdown_bounded(self):
        d = payload()
        # 34 disjoint PRs; only 30 are ever shown by the renderer.
        d['prs'] = []
        for i in range(1, 35):
            p = copy.deepcopy(payload()['prs'][0]); p['number'] = i; p['files'] = [f'changes/{i}.py']
            d['prs'].append(p)
        output = mod.markdown(mod.snapshot(d))
        self.assertIn('Nog 4 PR', output)
        self.assertNotIn('PR #34:', output)

    def test_cli_markdown(self):
        with tempfile.TemporaryDirectory() as t:
            path = pathlib.Path(t) / 'input.json'
            path.write_text(json.dumps(payload()))
            r = subprocess.run([sys.executable, str(SCRIPT), str(path), '--format', 'markdown'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn('Geen toestemming', r.stdout)
            self.assertNotIn('scripts/a.py', r.stdout)

    def test_cli_valid(self):
        with tempfile.TemporaryDirectory() as t:
            path = pathlib.Path(t) / 'input.json'
            path.write_text(json.dumps(payload()))
            r = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)['review_candidate_count'], 1)

if __name__ == '__main__':
    unittest.main()
