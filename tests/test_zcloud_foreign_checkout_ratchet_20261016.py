"""Offline contract: monotonic, disjoint, no runner/VPS side effects."""
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import unittest

MODULE = Path(__file__).parents[1] / 'scripts/zcloud_foreign_checkout_ratchet_20261016.py'
spec = importlib.util.spec_from_file_location('checkout_guard', MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
P = '.github/workflows/example.yml'
Q = '.github/actions/tool/action.yaml'


def workflow(repository=None, step='- uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803'):
    return 'name: Test\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      ' + step + ('\n        with:\n          repository: ' + repository if repository is not None else '') + '\n      - run: echo safe\n'


class CheckoutRepoGuardTest(unittest.TestCase):
    def test_current_repository_default_safe(self):
        self.assertFalse(guard.scan(workflow()))

    def test_explicit_same_repo_safe(self):
        self.assertFalse(guard.scan(workflow('Zennay/zCloud')))
        self.assertFalse(guard.scan(workflow('${{ github.repository }}')))

    def test_foreign_repository_rejected(self):
        self.assertTrue(guard.scan(workflow('other/repo')))

    def test_dynamic_repository_rejected(self):
        self.assertTrue(guard.scan(workflow('${{ github.event.pull_request.head.repo.full_name }}')))

    def test_duplicate_legacy_risk_rejected(self):
        old = workflow('other/repo')
        new = old.replace('      - run: echo safe', '      - uses: actions/checkout@v4\n        with:\n          repository: other/repo\n      - run: echo safe')
        self.assertEqual(guard.compare({P: old}, {P: new})[0]['new_count'], 1)

    def test_grandfather_existing_unchanged_risk(self):
        old = workflow('other/repo')
        self.assertEqual(guard.compare({P: old}, {P: old}), [])

    def test_remove_old_risk(self):
        self.assertEqual(guard.compare({P: workflow('other/repo')}, {P: workflow()}), [])

    def test_separate_path_risk_rejected(self):
        self.assertEqual(len(guard.compare({P: workflow('other/repo')}, {P: workflow('other/repo'), Q: workflow('other/repo')})), 1)

    def test_comment_ignored(self):
        self.assertFalse(guard.scan('# - uses: actions/checkout@v4\n# repository: untrusted/repo'))

    def test_noncanonical_checkout_cannot_sneak_through(self):
        self.assertIn('ambiguous-checkout-syntax', guard.scan('- name: test\n  run: echo actions/checkout@v4'))

    def test_quoted_foreign_repo_rejected(self):
        self.assertTrue(guard.scan(workflow('"other/repo"')))

    def test_other_step_repository_not_associated(self):
        text = workflow() + '      - uses: actions/setup-node@v4\n        with:\n          repository: other/repo\n'
        self.assertFalse(guard.scan(text))

    def test_paths_scoped_to_workflows_and_composites(self):
        self.assertEqual(guard.compare({'README.md': ''}, {'README.md': workflow('other/repo')}), [])

    def test_changed_whitespace_does_not_create_new_risk(self):
        old = workflow('other/repo')
        self.assertEqual(guard.compare({P: old}, {P: old.replace('repository: other', 'repository:  other')}), [])

    def test_alias_checkout_input_map_rejected(self):
        text = workflow().replace('      - run: echo safe', '        with: *external_inputs\n      - run: echo safe')
        self.assertIn('ambiguous-checkout-input-map', guard.scan(text))

    def test_merged_checkout_input_map_rejected(self):
        text = workflow().replace('      - run: echo safe', '        with:\n          <<: *external_inputs\n      - run: echo safe')
        self.assertIn('ambiguous-checkout-input-map', guard.scan(text))

    def test_dynamic_checkout_input_map_rejected(self):
        text = workflow().replace('      - run: echo safe', '        with: ${{ fromJSON(inputs.checkout) }}\n      - run: echo safe')
        self.assertIn('ambiguous-checkout-input-map', guard.scan(text))

    def test_checkout_input_anchor_declaration_rejected(self):
        text = workflow().replace('      - run: echo safe', '        with: &external_inputs\n      - run: echo safe')
        self.assertIn('ambiguous-checkout-input-map', guard.scan(text))

    def test_named_checkout_step_also_protected(self):
        text = workflow('foreign/repo', '- name: Fetch\n        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803')
        self.assertTrue(guard.scan(text))

    def test_aliased_checkout_action_denied_without_literal_checkout(self):
        text = workflow().replace(
            '- uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803',
            '- uses: *trusted_checkout',
        )
        self.assertIn('indirect-action-identity', guard.scan(text))

    def test_indirect_action_can_hide_foreign_checkout(self):
        text = 'defaults: &checkout_action actions/checkout@v4\n' + workflow('foreign/repo', '- uses: *checkout_action')
        findings = guard.scan(text)
        self.assertIn('indirect-action-identity', findings)

    def test_aliased_step_denied_without_uses_text(self):
        text = 'steps:\n    - *checkout_step\n'
        self.assertIn('indirect-action-identity', guard.scan(text))

    def test_inline_merged_step_denied(self):
        self.assertIn('indirect-action-identity', guard.scan('steps:\n    - <<: *checkout_step\n'))

    def test_dynamic_uses_expression_denied(self):
        self.assertIn(
            'indirect-action-identity',
            guard.scan('steps:\n    - uses: ${{ inputs.action_ref }}\n'),
        )

    def test_quoted_uses_alias_denied(self):
        self.assertIn(
            'indirect-action-identity',
            guard.scan('steps:\n    - uses: "*checkout_action"\n'),
        )

    def test_indirect_action_no_new_debt(self):
        old = 'steps:\n    - uses: *already_present\n'
        self.assertEqual(guard.compare({P: old}, {P: old}), [])
        new = old + '    - uses: *another_action\n'
        self.assertEqual(
            guard.compare({P: old}, {P: new})[0]['new_count'], 1,
        )

    def test_indirect_action_in_new_composite_rejected(self):
        self.assertTrue(guard.compare({}, {Q: 'runs:\n  using: composite\n  steps:\n    - *foreign_checkout\n'}))

    def test_indirection_in_comment_is_not_action(self):
        self.assertFalse(guard.scan('# - *checkout_step\n# - uses: *checkout_action\n'))

    def test_ordinary_action_and_shell_text_remain_safe(self):
        self.assertFalse(guard.scan('steps:\n    - uses: actions/setup-node@v4\n    - run: echo "*checkout"\n'))

    def test_composite_actions_outside_github_actions_are_checked(self):
        outside = 'tools/internal/action.yaml'
        self.assertTrue(guard.compare({}, {outside: workflow('external/vendor')}))

    def test_root_composite_action_is_checked(self):
        self.assertTrue(guard.compare({}, {'action.yml': 'steps:\n  - uses: *hidden_checkout\n'}))

    def test_action_named_similarly_does_not_expand_surface(self):
        self.assertEqual(
            guard.compare({}, {'tools/internal/my-action.yml': workflow('outside/repo')}),
            [],
        )

    def test_nonworkflow_nested_yaml_is_not_action_metadata(self):
        self.assertEqual(
            guard.compare({}, {'config/action-inputs.yaml': workflow('outside/repo')}),
            [],
        )

    def test_manifest_oversize_denied_before_git_show(self):
        from unittest.mock import patch
        calls = []
        def fake_git(_repo, *args):
            calls.append(args[0])
            if args[0] == 'ls-tree':
                return b'.github/workflows/example.yml\x00'
            if args[0] == 'cat-file':
                return str(guard.MAX_MANIFEST_BYTES + 1).encode('ascii')
            raise AssertionError('Oversized manifest must never be read')
        with patch.object(guard, 'git', side_effect=fake_git):
            with self.assertRaisesRegex(ValueError, 'single-file size budget'):
                guard.tree('.', 'a' * 40)
        self.assertNotIn('show', calls)

    def test_manifest_count_budget_denies_early(self):
        from unittest.mock import patch
        with patch.object(guard, 'git', return_value=b'.github/workflows/example.yml\x00'):
            with patch.object(guard, 'MAX_MANIFESTS', 0):
                with self.assertRaisesRegex(ValueError, 'Manifest count'):
                    guard.tree('.', 'a' * 40)

    def test_manifest_total_budget_denies_before_read(self):
        from unittest.mock import patch
        calls = []
        def fake_git(_repo, *args):
            calls.append(args[0])
            if args[0] == 'ls-tree':
                return b'action.yml\x00'
            if args[0] == 'cat-file':
                return b'1'
            raise AssertionError('Total budget breach must preempt read')
        with patch.object(guard, 'git', side_effect=fake_git):
            with patch.object(guard, 'MAX_INVENTORY_BYTES', 0):
                with self.assertRaisesRegex(ValueError, 'total byte budget'):
                    guard.tree('.', 'a' * 40)
        self.assertNotIn('show', calls)

    def test_manifest_blob_size_disagreement_denied(self):
        from unittest.mock import patch
        def fake_git(_repo, *args):
            if args[0] == 'ls-tree':
                return b'action.yml\x00'
            if args[0] == 'cat-file':
                return b'999'
            if args[0] == 'show':
                return b'YAML'
            raise AssertionError('Unexpected git operation')
        with patch.object(guard, 'git', side_effect=fake_git):
            with self.assertRaisesRegex(ValueError, 'size mismatch'):
                guard.tree('.', 'a' * 40)

    def test_canonical_main_accepts_exact_sha(self):
        from unittest.mock import patch
        sha = 'a' * 40
        def fake_git(repo, *args):
            if args[:3] == ('remote', 'get-url', 'origin'):
                return b'https://github.com/Zennay/zCloud.git\n'
            if args[-3:] == ('--exit-code', 'origin', 'refs/heads/main'):
                return (sha + '\trefs/heads/main\n').encode('ascii')
            raise AssertionError(args)
        with patch.object(guard, 'git', side_effect=fake_git):
            guard.verify_current_main('.', sha)

    def test_canonical_main_url_without_git_suffix_accepted(self):
        from unittest.mock import patch
        sha = 'e' * 40
        with patch.object(guard, 'git', side_effect=[
            b'https://github.com/Zennay/zCloud\n',
            (sha + '\trefs/heads/main\n').encode('ascii'),
        ]):
            guard.verify_current_main('.', sha)

    def test_foreign_git_remote_denied_before_network(self):
        from unittest.mock import patch
        with patch.object(guard, 'git', return_value=b'https://github.com/attacker/zCloud.git\n') as fake:
            with self.assertRaisesRegex(ValueError, 'canonical'):
                guard.verify_current_main('.', 'a' * 40)
        self.assertEqual(fake.call_count, 1)

    def test_ssh_origin_not_mistaken_for_canonical_https(self):
        from unittest.mock import patch
        with patch.object(guard, 'git', return_value=b'git@github.com:Zennay/zCloud.git\n'):
            with self.assertRaises(ValueError):
                guard.verify_current_main('.', 'a' * 40)

    def test_current_main_sha_mismatch_denied(self):
        from unittest.mock import patch
        with patch.object(guard, 'git', side_effect=[
            b'https://github.com/Zennay/zCloud.git\n',
            ('b' * 40 + '\trefs/heads/main\n').encode('ascii'),
        ]):
            with self.assertRaisesRegex(ValueError, 'differs'):
                guard.verify_current_main('.', 'a' * 40)

    def test_wrong_advertised_ref_denied(self):
        from unittest.mock import patch
        with patch.object(guard, 'git', side_effect=[
            b'https://github.com/Zennay/zCloud.git\n',
            ('a' * 40 + '\trefs/heads/staging\n').encode('ascii'),
        ]):
            with self.assertRaisesRegex(ValueError, 'identity'):
                guard.verify_current_main('.', 'a' * 40)

    def test_multiple_advertisements_denied(self):
        from unittest.mock import patch
        line = 'a' * 40 + '\trefs/heads/main\n'
        with patch.object(guard, 'git', side_effect=[
            b'https://github.com/Zennay/zCloud.git\n',
            (line + line).encode('ascii'),
        ]):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                guard.verify_current_main('.', 'a' * 40)

    def test_invalid_sha_advertisement_denied(self):
        from unittest.mock import patch
        with patch.object(guard, 'git', side_effect=[
            b'https://github.com/Zennay/zCloud.git\n',
            b'HEAD\trefs/heads/main\n',
        ]):
            with self.assertRaisesRegex(ValueError, 'identity'):
                guard.verify_current_main('.', 'a' * 40)

    def test_main_proof_rechecked_after_inventory(self):
        from unittest.mock import patch
        sha = 'a' * 40
        with patch.object(guard, 'verify_current_main', side_effect=[None, ValueError('main moved')]) as fresh:
            with patch.object(guard, 'tree', side_effect=[{}, {}]):
                self.assertEqual(guard.main(['--base', sha, '--head', 'b'*40, '--require-current-main']), 2)
        self.assertEqual(fresh.call_count, 2)

    def test_git_timeout_is_indeterminate(self):
        from unittest.mock import patch
        with patch.object(guard.subprocess, 'run', side_effect=guard.subprocess.TimeoutExpired('git', 20)):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                guard.git('.', 'ls-tree', '-r', 'a'*40)

    def test_trusted_admission_precedes_first_pr_python_execution(self):
        workflow_path = Path(__file__).parents[1] / '.github/workflows/zcloud-foreign-checkout-origin-ratchet-20261016.yml'
        text = workflow_path.read_text(encoding='utf-8')
        admission = text.index('- name: Admit canonical current main before executing PR code')
        test_exec = text.index('python3 -m unittest -v tests.test_zcloud_foreign_checkout_ratchet_20261016')
        self.assertLess(admission, test_exec)

    def test_precode_admission_uses_exact_canonical_main(self):
        workflow_path = Path(__file__).parents[1] / '.github/workflows/zcloud-foreign-checkout-origin-ratchet-20261016.yml'
        text = workflow_path.read_text(encoding='utf-8')
        self.assertIn('origin="$(git remote get-url origin)"', text)
        self.assertIn('http.followRedirects=false ls-remote --exit-code origin refs/heads/main', text)
        self.assertIn('test "$advertised_sha" = "$EXPECTED_BASE"', text)
        self.assertIn('test "$advertised_ref" = refs/heads/main', text)

    def test_precode_admission_never_checks_out_foreign_repository(self):
        workflow_path = Path(__file__).parents[1] / '.github/workflows/zcloud-foreign-checkout-origin-ratchet-20261016.yml'
        text = workflow_path.read_text(encoding='utf-8')
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}', text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('test "$HEAD_REPO" = "$REPO"', text)

    def test_scanner_requires_main_proof_after_running_pr_unit_tests(self):
        workflow_path = Path(__file__).parents[1] / '.github/workflows/zcloud-foreign-checkout-origin-ratchet-20261016.yml'
        text = workflow_path.read_text(encoding='utf-8')
        self.assertIn('--require-current-main', text)
        self.assertIn('git diff --quiet -- scripts/zcloud_foreign_checkout_ratchet_20261016.py', text)

    def test_hosted_workflow_step_keys_do_not_duplicate(self):
        workflow_path = Path(__file__).parents[1] / '.github/workflows/zcloud-foreign-checkout-origin-ratchet-20261016.yml'
        lines = workflow_path.read_text(encoding='utf-8').splitlines()
        lines = lines[lines.index('    steps:') + 1:]
        starts = [i for i, line in enumerate(lines) if line.startswith('      - ') ]
        self.assertEqual(len(starts), 5)
        starts.append(len(lines))
        for first, last in zip(starts, starts[1:]):
            seen = set()
            for line in lines[first:last]:
                if not line.startswith('        ') or line.startswith('          '):
                    continue
                key = line.strip().split(':', 1)[0]
                if key in ('name', 'uses', 'with', 'env', 'run', 'shell'):
                    self.assertNotIn(key, seen, (first, key))
                    seen.add(key)
            self.assertTrue('run' in seen or 'uses' in seen, first)

    def test_bad_refs_deny(self):
        self.assertEqual(guard.main(['--base', 'main', '--head', 'HEAD']), 2)

    def test_git_tree_acceptance(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(['git', 'init', '-q', td], check=True)
            subprocess.run(['git', '-C', td, 'config', 'user.name', 'test'], check=True)
            subprocess.run(['git', '-C', td, 'config', 'user.email', 'test@example.com'], check=True)
            p = Path(td) / P
            p.parent.mkdir(parents=True)
            p.write_text(workflow(), encoding='utf-8')
            subprocess.run(['git', '-C', td, 'add', '.'], check=True)
            subprocess.run(['git', '-C', td, 'commit', '-qm', 'baseline'], check=True)
            old = subprocess.check_output(['git', '-C', td, 'rev-parse', 'HEAD'], text=True).strip()
            p.write_text(workflow('evil/repo'), encoding='utf-8')
            subprocess.run(['git', '-C', td, 'commit', '-qam', 'bad'], check=True)
            new = subprocess.check_output(['git', '-C', td, 'rev-parse', 'HEAD'], text=True).strip()
            self.assertEqual(guard.main(['--repo', td, '--base', old, '--head', new]), 1)


if __name__ == '__main__':
    unittest.main()
