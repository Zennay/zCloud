"""Deterministic deny-first PR/PR-less ownership admission regressions."""
import copy
from datetime import datetime, timedelta, timezone
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
    return {'main_sha': S, 'observed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'inventory': {'prs_complete': True, 'branches_complete': True},
            'branch_inventory_receipt': {
                'schema': 'zcloud-prless-branch-snapshot-v1',
                'inventory_complete': True, 'admission_authorized': False,
                'deployment_authorized': False, 'paths_compared': False,
                'main_sha': S, 'branch_count': 1, 'open_pr_head_count': 1,
                'branch_pages': 1, 'pr_pages': 1, 'pr_less': []
            },
            'gates': {'580': 'open', '1089': 'open', '1135': 'open'},
            'prs': [{'number': 12, 'head_sha': H, 'base_sha': S,
                     'draft': False, 'owner_approved': True, 'files': ['scripts/a.py'], 'previous_files': [],
                     'files_evidence': {'complete': True, 'total_count': 1,
                         'rename_sources_complete': True, 'previous_files_total': 0,
                         'head_sha': H, 'base_sha': S}, 'required_checks': ['tests'],
                     'checks': [{'name': 'tests', 'head_sha': H, 'conclusion': 'success'}]}],
            'branches': []}

def snap(d):
    """Produce matching synthetic #1312 receipt for local tests with branches."""
    # Synthetic complete diff evidence; targeted malformed tests bypass this helper.
    for item in d['prs'] + d['branches']:
        if isinstance(item.get('files_evidence'), dict):
            item.setdefault('previous_files', [])
            item['files_evidence'].setdefault('rename_sources_complete', True)
            item['files_evidence'].setdefault('previous_files_total', len(item['previous_files']))
    receipt = d['branch_inventory_receipt']
    receipt['pr_less'] = [
        {'branch': b['name'], 'commit_sha': b['head_sha']} for b in d['branches']
    ]
    receipt['branch_count'] = 1 + len(d['branches'])
    return mod.snapshot(d)


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
        self.assertEqual(snap(d)['review_candidates'][0]['reasons'], ['stale_base'])

    def test_draft_owner_unapproved(self):
        d = payload(); d['prs'][0]['draft'] = True; d['prs'][0]['owner_approved'] = False
        self.assertEqual(snap(d)['review_candidate_count'], 0)
        self.assertIn('draft', snap(d)['review_candidates'][0]['reasons'])

    def test_old_head_green_denied(self):
        d = payload(); d['prs'][0]['checks'][0]['head_sha'] = S
        self.assertIn('exact_head_checks_not_green', snap(d)['review_candidates'][0]['reasons'])

    def test_failed_or_skipped_checks_denied(self):
        for state in ('failure', 'pending', 'cancelled', 'skipped', 'neutral'):
            d = payload(); d['prs'][0]['checks'][0]['conclusion'] = state
            self.assertEqual(snap(d)['review_candidate_count'], 0)

    def test_open_draft_pr_still_reserves_files(self):
        d = payload(); other = copy.deepcopy(d['prs'][0]); other['number'] = 13
        other['draft'] = True; d['prs'].append(other)
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_prless_branch_collision_denied(self):
        d = payload(); d['branches'] = [{'name': 'owner-active', 'head_sha': H, 'files': ['scripts/a.py'], 'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}}]
        self.assertEqual(snap(d)['review_candidate_count'], 0)

    def test_disjoint_branch_not_blocking(self):
        d = payload(); d['branches'] = [{'name': 'owner-active', 'head_sha': H, 'files': ['scripts/b.py'], 'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}}]
        self.assertEqual(snap(d)['review_candidate_count'], 1)

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
            snap(d)

    def test_zero_change_pr_denied(self):
        d = payload()
        d['prs'][0]['files_evidence']['total_count'] = 0
        d['prs'][0]['files'] = []
        with self.assertRaises(mod.InvalidEvidence):
            snap(d)

    def test_empty_unmerged_branch_is_safe(self):
        d = payload()
        d['branches'] = [{'name': 'empty', 'head_sha': H, 'files': [],
                          'files_evidence': {'complete': True, 'total_count': 0, 'head_sha': H, 'base_sha': S}}]
        self.assertEqual(snap(d)['review_candidate_count'], 1)

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
        report = snap(d)
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
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_ancestry_index_marks_both_colliding_prs(self):
        d = payload()
        other = copy.deepcopy(d['prs'][0])
        other['number'] = 13
        other['files'] = ['scripts/a.py/inner']
        d['prs'].append(other)
        report = snap(d)
        self.assertEqual(report['review_candidate_count'], 0)
        self.assertTrue(all('overlapping_owner' in p['reasons'] for p in report['review_candidates']))

    def test_pr_rename_old_path_reserves_other_worker(self):
        d = payload()
        d['prs'][0]['files'] = ['src/new-name.py']
        d['prs'][0]['previous_files'] = ['src/old-name.py']
        d['prs'][0]['files_evidence']['previous_files_total'] = 1
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['src/old-name.py'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_pr_rename_new_path_reserves_other_worker(self):
        d = payload()
        d['prs'][0]['files'] = ['src/new-name.py']
        d['prs'][0]['previous_files'] = ['src/old-name.py']
        d['prs'][0]['files_evidence']['previous_files_total'] = 1
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['src/new-name.py'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_missing_rename_source_coverage_denied(self):
        d = payload()
        d['prs'][0]['files_evidence']['rename_sources_complete'] = False
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_truncated_rename_sources_denied(self):
        d = payload()
        d['prs'][0]['files_evidence']['previous_files_total'] = 1
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_zero_change_branch_cannot_claim_renamed_files(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': [],
            'previous_files': ['src/old.py'],
            'files_evidence': {'complete': True, 'total_count': 0,
                               'rename_sources_complete': True, 'previous_files_total': 1,
                               'head_sha': H, 'base_sha': S}
        }]
        with self.assertRaises(mod.InvalidEvidence):
            snap(d)

    def test_file_to_directory_swap_collides(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['scripts/a.py/subfile'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_directory_to_file_swap_collides(self):
        d = payload()
        d['prs'][0]['files'] = ['scripts/a.py/subfile']
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['scripts/a.py'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_case_only_file_collision_rejected(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['SCRIPTS/A.PY'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_nfc_normalized_file_collision_rejected(self):
        d = payload()
        d['prs'][0]['files'] = ['src/café.py']
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['src/cafe\u0301.py'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertIn('overlapping_owner', snap(d)['review_candidates'][0]['reasons'])

    def test_unicode_duplicate_within_pr_denied(self):
        d = payload()
        d['prs'][0]['files'] = ['src/café.py', 'src/cafe\u0301.py']
        d['prs'][0]['files_evidence']['total_count'] = 2
        with self.assertRaises(mod.InvalidEvidence):
            snap(d)

    def test_sibling_names_are_disjoint(self):
        d = payload()
        d['branches'] = [{
            'name': 'other', 'head_sha': H, 'files': ['scripts/a.py-next'],
            'files_evidence': {'complete': True, 'total_count': 1, 'head_sha': H, 'base_sha': S}
        }]
        self.assertEqual(snap(d)['review_candidate_count'], 1)

    def test_prless_missing_receipt_denied(self):
        d = payload()
        del d['branch_inventory_receipt']
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_other_main_sha_denied(self):
        d = payload()
        d['branch_inventory_receipt']['main_sha'] = H
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_false_complete_denied(self):
        d = payload()
        d['branch_inventory_receipt']['inventory_complete'] = False
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_receipt_unexpected_branch_denied(self):
        d = payload()
        d['branch_inventory_receipt']['pr_less'] = [{'branch': 'unexpected', 'commit_sha': H}]
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_receipt_missing_branch_denied(self):
        d = payload()
        d['branches'] = [{'name': 'owner', 'head_sha': H, 'files': [],
                          'files_evidence': {'complete': True, 'total_count': 0, 'head_sha': H, 'base_sha': S}}]
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_receipt_wrong_branch_sha_denied(self):
        d = payload()
        d['branches'] = [{'name': 'owner', 'head_sha': H, 'files': [],
                          'files_evidence': {'complete': True, 'total_count': 0, 'head_sha': H, 'base_sha': S}}]
        d['branch_inventory_receipt']['pr_less'] = [{'branch': 'owner', 'commit_sha': S}]
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_receipt_authority_spoof_denied(self):
        d = payload()
        d['branch_inventory_receipt']['admission_authorized'] = True
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_prless_invalid_page_count_denied(self):
        d = payload()
        d['branch_inventory_receipt']['branch_pages'] = 0
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(d)

    def test_time_window_accepts_now_and_300_seconds(self):
        base = datetime(2026, 10, 14, 1, 0, 0, tzinfo=timezone.utc)
        d = payload()
        for age in (0, 300, -30):
            d['observed_at'] = (base - timedelta(seconds=age)).strftime('%Y-%m-%dT%H:%M:%SZ')
            self.assertEqual(mod.snapshot(d, now=base)['review_candidate_count'], 1)

    def test_time_window_rejects_stale_or_future(self):
        base = datetime(2026, 10, 14, 1, 0, 0, tzinfo=timezone.utc)
        d = payload()
        for age in (301, 3600, -31, -3600):
            d['observed_at'] = (base - timedelta(seconds=age)).strftime('%Y-%m-%dT%H:%M:%SZ')
            with self.assertRaises(mod.InvalidEvidence):
                mod.snapshot(d, now=base)

    def test_time_window_requires_unambiguous_utc(self):
        for bad in ('2026-10-14T00:00:00', '2026-10-14T00:00:00+00:00',
                    '2026-10-14T00:00:00.000Z', '2026-02-30T00:00:00Z',
                    'yesterday', 42, True):
            d = payload()
            d['observed_at'] = bad
            with self.assertRaises(mod.InvalidEvidence):
                mod.snapshot(d)

    def test_invalid_clock_rejected(self):
        with self.assertRaises(mod.InvalidEvidence):
            mod.snapshot(payload(), now=datetime(2026, 10, 14))

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
        self.assertIn('required_checks_missing', snap(d)['review_candidates'][0]['reasons'])

    def test_empty_required_check_policy_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(required_checks=[]))

    def test_duplicate_required_check_policy_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(required_checks=['tests', 'tests']))

    def test_empty_checks_denied(self):
        self.assert_denied(lambda d: d['prs'][0].update(checks=[]))

    def test_cli_refuses_symlink_without_following(self):
        with tempfile.TemporaryDirectory() as t:
            private = pathlib.Path(t) / 'private.json'
            private.write_text('{"secret":"hidden"}')
            link = pathlib.Path(t) / 'manifest.json'
            link.symlink_to(private)
            r = subprocess.run([sys.executable, str(SCRIPT), str(link)],
                               capture_output=True, text=True, timeout=5)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn('hidden', r.stdout + r.stderr)
            self.assertFalse(json.loads(r.stdout)['release_authorized'])

    @unittest.skipUnless(hasattr(__import__('os'), 'mkfifo'), 'requires POSIX FIFO')
    def test_cli_refuses_fifo_without_hanging(self):
        import os
        with tempfile.TemporaryDirectory() as t:
            fifo = pathlib.Path(t) / 'pipe.json'
            os.mkfifo(fifo)
            r = subprocess.run([sys.executable, str(SCRIPT), str(fifo)],
                               capture_output=True, text=True, timeout=5)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(json.loads(r.stdout)['release_authorized'])

    def test_cli_refuses_oversized_regular_file(self):
        with tempfile.TemporaryDirectory() as t:
            big = pathlib.Path(t) / 'big.json'
            big.write_bytes(b' ' * (mod.MAX_BYTES + 1))
            r = subprocess.run([sys.executable, str(SCRIPT), str(big)],
                               capture_output=True, text=True, timeout=5)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(json.loads(r.stdout)['error'], 'oversized_snapshot')

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

    def test_grouped_blocker_counts_and_no_double_count_per_reason(self):
        d = payload()
        second = copy.deepcopy(d['prs'][0])
        second['number'] = 13
        second['files'] = ['scripts/b.py']
        second['draft'] = True
        second['owner_approved'] = False
        second['checks'][0]['head_sha'] = S
        d['prs'].append(second)
        report = snap(d)
        self.assertEqual(report['blocked_candidate_count'], 1)
        self.assertEqual(report['blocker_counts']['draft'], 1)
        self.assertEqual(report['blocker_counts']['owner_review_missing'], 1)
        self.assertEqual(report['blocker_counts']['exact_head_checks_not_green'], 1)
        self.assertEqual(report['blocker_counts']['overlapping_owner'], 0)
        display = mod.markdown(report)
        self.assertIn('Nog niet reviewgereed: **1**', display)
        self.assertIn('concept-PR: **1', display)
        self.assertNotIn('scripts/b.py', display)

    def test_grouped_blockers_cap_show_three_even_if_thirty_pr_limit(self):
        d = payload()
        d['prs'] = []
        for n in range(1, 35):
            p = copy.deepcopy(payload()['prs'][0])
            p['number'] = n
            p['draft'] = True
            p['files'] = [f'path/item-{n}.py']
            d['prs'].append(p)
        report = snap(d)
        self.assertEqual(report['blocked_candidate_count'], 34)
        self.assertEqual(report['blocker_counts']['draft'], 34)
        display = mod.markdown(report)
        self.assertIn('concept-PR: **34', display)
        self.assertIn('Nog 4 PR', display)

    def test_markdown_never_authorizes_release(self):
        d = payload(); d['gates'] = dict.fromkeys(d['gates'], 'closed')
        report = mod.markdown(snap(d))
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
        output = mod.markdown(snap(d))
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
