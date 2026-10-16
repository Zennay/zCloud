"""Real-Git acceptance cases for cross-run artifact download review ratchet."""
import importlib.util
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_cross_run_artifact_gate_20261016.py"
spec = importlib.util.spec_from_file_location("artifact_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SAFE = """name: smoke
on: pull_request
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: Save evidence
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093
        with:
          name: evidence
"""
CROSS = """name: smoke
on: pull_request
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: Recover old evidence
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093
        with:
          name: evidence
          run-id: 1234
          github-token: DYNAMIC_VALUE
""".replace('DYNAMIC_VALUE', chr(36)+'{{ github.token }}')

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.workflow = self.repo / '.github/workflows/sample.yml'
        self.workflow.parent.mkdir(parents=True)
        self.git('init', '-q')
        self.git('config','user.email','tests@example.invalid')
        self.git('config','user.name','CI')
        # Avoid background Git auto-maintenance racing TemporaryDirectory
        # cleanup on short-lived real-Git fixtures in hosted CI.
        self.git('config','gc.auto','0')
        self.git('config','maintenance.auto','false')

    def git(self, *args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, value):
        self.workflow.write_text(value,encoding='utf-8')
        self.git('add','.')
        self.git('commit','-qm','fixture')
        return self.git('rev-parse','HEAD')

    def test_new_remote_run_fails(self):
        base=self.commit(SAFE); head=self.commit(CROSS)
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_unchanged_legacy_grandfathered(self):
        base=self.commit(CROSS)
        (self.repo/'.github/workflows/other.yml').write_text(SAFE)
        self.git('add','.'); self.git('commit','-qm','safe add'); head=self.git('rev-parse','HEAD')
        report=module.evaluate(self.repo,base,head)
        self.assertTrue(report['ok'])
        self.assertEqual(report['legacy_findings'],1)

    def test_duplication_of_legacy_denied(self):
        base=self.commit(CROSS)
        (self.repo/'.github/workflows/copied.yml').write_text(CROSS)
        self.git('add','.'); self.git('commit','-qm','copy'); head=self.git('rev-parse','HEAD')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_removal_allowed(self):
        base=self.commit(CROSS); head=self.commit(SAFE)
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],0)

    def test_rejects_stale_head(self):
        base=self.commit(SAFE); head=self.commit(CROSS)
        with self.assertRaises(module.Refusal):
            module.evaluate(self.repo,head,base)

    def test_rejects_symlink(self):
        base=self.commit(SAFE)
        self.workflow.unlink(); self.workflow.symlink_to('/etc/passwd')
        self.git('add','.'); self.git('commit','-qm','link'); head=self.git('rev-parse','HEAD')
        with self.assertRaises(module.Refusal):
            module.evaluate(self.repo,base,head)

    def test_invalid_base_non_ancestor_denied(self):
        base=self.commit(SAFE); head=self.commit(CROSS)
        self.assertEqual(self.git('rev-parse','HEAD~1'),base)
        with self.assertRaises(module.Refusal):
            module.evaluate(self.repo,'f'*40,head)

    def test_same_ref_denied(self):
        head=self.commit(SAFE)
        with self.assertRaises(module.Refusal):
            module.evaluate(self.repo,head,head)

    def test_repository_only_requires_review(self):
        value=SAFE.replace('          name: evidence',
                           '          name: evidence\n          repository: another/repo')
        self.assertEqual(sum(module._risks(value,'w.yml').values()),1)

    def test_with_flow_mapping_refused(self):
        value=SAFE.replace('with:\n          name: evidence',
                           'with: { run-id: 1234, github-token: token }')
        with self.assertRaises(module.Refusal):
            module._risks(value,'w.yml')

    def test_copy_identical_step_in_same_workflow_denied(self):
        base=self.commit(CROSS)
        head=self.commit(CROSS + CROSS[CROSS.index('      - name:'):])
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_preceding_with_mapping_denied(self):
        text = SAFE.replace(
            '        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093\n        with:\n          name: evidence',
            '        with:\n          run-id: 4321\n        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093',
        )
        with self.assertRaises(module.Refusal):
            module._risks(text,'w.yml')

    def test_preceding_dash_with_mapping_denied(self):
        text = SAFE.replace(
            '- name: Save evidence\n        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093\n        with:\n          name: evidence',
            '- with:\n          github-token: token\n        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093',
        )
        with self.assertRaises(module.Refusal):
            module._risks(text,'w.yml')

    def test_dash_uses_with_mapping_detected(self):
        text = SAFE.replace(
            '- name: Save evidence\n        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093',
            '- uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093',
        ).replace('          name: evidence','          run-id: 1234')
        self.assertEqual(sum(module._risks(text,'w.yml').values()),1)

    def test_git_streaming_refuses_oversize_early(self):
        bindir = self.repo / 'fake-bin'
        bindir.mkdir()
        binary = bindir / 'git'
        binary.write_text('#!/usr/bin/env python3\nimport sys\nsys.stdout.write("x"*1000000)\n', encoding='utf-8')
        binary.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(bindir)+os.pathsep+os.environ["PATH"]}):
            with self.assertRaisesRegex(module.Refusal, "git_output_oversized"):
                module._git(self.repo, 'status', max_bytes=512)

    def test_composite_manifest_new_cross_run_denied(self):
        base=self.commit(SAFE)
        manifest=self.repo/'custom/helpers/level-three/action.yml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(CROSS.replace('on: pull_request','runs:\n  using: composite'),encoding='utf-8')
        self.git('add','.'); self.git('commit','-qm','new nested composite')
        head=self.git('rev-parse','HEAD')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_composite_manifest_previous_debt_grandfathered(self):
        manifest=self.repo/'pkg/step/action.yaml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(CROSS,encoding='utf-8')
        base=self.commit(SAFE)
        head=self.commit(SAFE+'\n# harmless note\n')
        proof=module.evaluate(self.repo,base,head)
        self.assertTrue(proof['ok'])
        self.assertEqual(proof['legacy_findings'],1)

    def test_composite_manifest_duplication_denied(self):
        original=self.repo/'pkg/action.yml'
        original.parent.mkdir(parents=True)
        original.write_text(CROSS)
        base=self.commit(SAFE)
        other=self.repo/'another/deep/pkg/action.yaml'
        other.parent.mkdir(parents=True)
        other.write_text(CROSS)
        self.git('add','.'); self.git('commit','-qm','copy composite')
        head=self.git('rev-parse','HEAD')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_composite_manifest_symlink_refused(self):
        base=self.commit(SAFE)
        link=self.repo/'nested/action.yml'
        link.parent.mkdir()
        link.symlink_to('/etc/passwd')
        self.git('add','.'); self.git('commit','-qm','symlink action')
        head=self.git('rev-parse','HEAD')
        with self.assertRaises(module.Refusal):
            module.evaluate(self.repo,base,head)

    def test_unrelated_plain_yaml_does_not_claim_action_manifest(self):
        base=self.commit(SAFE)
        other=self.repo/'docs/overview.yml'
        other.parent.mkdir()
        other.write_text(CROSS)
        self.git('add','.'); self.git('commit','-qm','non runnable doc')
        head=self.git('rev-parse','HEAD')
        self.assertTrue(module.evaluate(self.repo,base,head)['ok'])

    def test_untrusted_pr_runs_admission_instead_of_skipping(self):
        workflow=(SCRIPT.parent.parent/'.github/workflows/zcloud-cross-run-artifact-review-20261016.yml').read_text()
        self.assertIn('Reject untrusted PR provenance before any repository checkout',workflow)
        self.assertNotIn('  deny-new-cross-run-downloads:\n    if:', workflow)
        self.assertLess(
            workflow.index('Reject untrusted PR provenance before any repository checkout'),
            workflow.index('uses: actions/checkout@'),
        )
        self.assertIn('test "$GITHUB_TRIGGERING_ACTOR" = Zennay',workflow)
        self.assertIn('test "$PR_HEAD_REPOSITORY" = "$GITHUB_REPOSITORY"',workflow)

    def test_untrusted_pr_has_no_live_runner_or_credential_writes(self):
        workflow=(SCRIPT.parent.parent/'.github/workflows/zcloud-cross-run-artifact-review-20261016.yml').read_text()
        self.assertIn('runs-on: ubuntu-latest',workflow)
        self.assertIn('contents: read',workflow)
        self.assertIn('persist-credentials: false',workflow)
        self.assertNotIn('runs-on: [self-hosted',workflow)
        self.assertNotIn('workflow_dispatch:',workflow)

    def test_case_variant_artifact_action_has_same_critical_risk(self):
        candidate=CROSS.replace('actions/download-artifact@','Actions/Download-Artifact@')
        self.assertEqual(sum(module._risks(candidate,'nested/action.yml').values()),1)

    def test_case_variant_artifact_addition_fails_with_real_git(self):
        base=self.commit(SAFE)
        head=self.commit(CROSS.replace('actions/download-artifact@','ACTIONS/DOWNLOAD-ARTIFACT@'))
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_case_variant_ambiguous_alias_denied(self):
        value='    uses: "*Actions/Download-Artifact@'+ 'd3f86a106a0bac45b974a628896c90dbdf5c8093"'
        with self.assertRaises(module.Refusal):
            module._risks(value,'w.yml')

    def test_moving_legacy_step_within_same_file_denied(self):
        base=self.commit(CROSS)
        moved=CROSS.replace('      - name: Recover old evidence',
                             '      # change step placement\n      - name: Recover old evidence')
        head=self.commit(moved)
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_unrelated_workflow_edit_after_legacy_step_allowed(self):
        base=self.commit(CROSS)
        head=self.commit(CROSS+'\n# other documentation\n')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],0)

    def test_uppercase_cross_run_input_is_not_ignored(self):
        modified=SAFE.replace('name: evidence','RUN-ID: 1234')
        self.assertEqual(sum(module._risks(modified,'w.yml').values()),1)

    def test_case_variant_duplicate_cross_run_input_denied(self):
        modified=CROSS.replace('          github-token:', '          RUN-ID: 999\n          github-token:')
        with self.assertRaises(module.Refusal):
            module._risks(modified,'w.yml')

    def test_gh_run_download_direct_step_is_review_debt(self):
        source=SAFE+'\n      - name: Download old run\n        run: gh run download 12345\n'
        self.assertEqual(sum(module._risks(source,'w.yml').values()),1)

    def test_gh_run_download_repository_flag_is_review_debt(self):
        self.assertEqual(
            sum(module._risks('      run: gh -R Zennay/other run download 12345','w.yml').values()),1
        )
        self.assertEqual(
            sum(module._risks('      run: /usr/local/bin/gh --repo=Zennay/other run download 12345','w.yml').values()),1
        )

    def test_gh_run_download_split_shell_line_is_review_debt(self):
        code='      run: |\n        gh run '+chr(92)+'\n          download 12345\n'
        self.assertEqual(sum(module._risks(code,'w.yml').values()),1)

    def test_gh_run_download_case_variation_is_review_debt(self):
        self.assertEqual(sum(module._risks('      run: GH RUN DOWNLOAD 1234','w.yml').values()),1)

    def test_gh_comment_and_unrelated_cli_do_not_create_debt(self):
        source='# gh run download 1234\n      run: gh run view 1234\n'
        self.assertEqual(sum(module._risks(source,'w.yml').values()),0)

    def test_gh_run_download_new_real_git_workflow_denied(self):
        base=self.commit(SAFE)
        head=self.commit(SAFE+'\n      - name: Old run\n        run: gh run download 12345\n')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_gh_run_download_unchanged_legacy_is_inventory_only(self):
        base=self.commit(SAFE+'\n      - name: Old run\n        run: gh run download 12345\n')
        head=self.commit(SAFE+'\n      - name: Old run\n        run: gh run download 12345\n# innocent footer\n')
        r=module.evaluate(self.repo,base,head)
        self.assertTrue(r['ok'])
        self.assertEqual(r['legacy_findings'],1)
        self.assertEqual(r['new_findings'],0)

    def test_gh_run_download_nested_composite_action_denied(self):
        base=self.commit(SAFE)
        manifest=self.repo/'tools/inner/action.yml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('name: inner\nruns:\n  using: composite\n  steps:\n    - run: gh run download 12345\n      shell: bash\n')
        self.git('add','.'); self.git('commit','-qm','new composite download')
        head=self.git('rev-parse','HEAD')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_candidate_policy_downgrade_cannot_change_immutable_base(self):
        script=self.repo/'scripts/admission.py'
        script.parent.mkdir()
        script.write_text('raise SystemExit(7)\n')
        base=self.commit(SAFE)
        script.write_text('raise SystemExit(0)\n')
        head=self.commit(SAFE+'\n# policy hijack\n')
        self.assertNotEqual(base,head)
        checkout_script=self.git('show',base+':scripts/admission.py')
        self.assertEqual(checkout_script,'raise SystemExit(7)')
        self.assertEqual(script.read_text(),'raise SystemExit(0)\n')

    def test_workflow_runs_base_policy_first_after_bootstrap(self):
        workflow=(SCRIPT.parent.parent/'.github/workflows/zcloud-cross-run-artifact-review-20261016.yml').read_text()
        self.assertIn('git show "$PR_BASE:$scanner" > "$policy"',workflow)
        self.assertIn('python3 "$policy" --repo-root . --base "$PR_BASE" --head "$PR_HEAD"',workflow)
        self.assertIn('test "$PR_BASE" = ',workflow)
        self.assertIn('ZCLOUD_CROSS_RUN_BASE_OWNED_POLICY_GREEN=1',workflow)
        self.assertLess(workflow.index('python3 "$policy"'),workflow.index('python3 "$scanner"'))
        self.assertNotIn('git show "$PR_HEAD:$scanner" > "$policy"',workflow)

    def test_bootstrap_exception_exact_single_main_sha_only(self):
        workflow=(SCRIPT.parent.parent/'.github/workflows/zcloud-cross-run-artifact-review-20261016.yml').read_text()
        self.assertIn('d813cbe70ae5291246bc8f4977cba18bc066519a',workflow)
        self.assertIn('git cat-file -e "$PR_BASE:$scanner"',workflow)
        self.assertIn('test "$PR_BASE" = ',workflow)
        self.assertIn('ZCLOUD_CROSS_RUN_FIRST_INTEGRATION_BOOTSTRAP=1',workflow)

    def test_third_party_download_action_run_id_creates_review_debt(self):
        candidate=SAFE.replace('actions/download-artifact@','dawidd6/action-download-artifact@')
        candidate=candidate.replace('name: evidence','run_id: 12345')
        self.assertEqual(sum(module._risks(candidate,'w.yml').values()),1)

    def test_third_party_download_action_github_token_creates_review_debt(self):
        candidate=SAFE.replace('actions/download-artifact@','dawidd6/action-download-artifact@')
        candidate=candidate.replace('name: evidence','github_token: TOKEN_PLACEHOLDER')
        self.assertEqual(sum(module._risks(candidate,'w.yml').values()),1)

    def test_third_party_download_action_workflow_and_branch_require_review(self):
        vendor=SAFE.replace('actions/download-artifact@','dawidd6/action-download-artifact@')
        self.assertEqual(sum(module._risks(vendor.replace('name: evidence','workflow: build.yml'),'w.yml').values()),1)
        self.assertEqual(sum(module._risks(vendor.replace('name: evidence','branch: main'),'w.yml').values()),1)

    def test_third_party_download_action_in_new_nested_manifest_denied(self):
        base=self.commit(SAFE)
        manifest=self.repo/'vendor/steps/action.yaml'
        manifest.parent.mkdir(parents=True)
        value=SAFE.replace('actions/download-artifact@','dawidd6/action-download-artifact@')
        manifest.write_text(value.replace('name: evidence','run_id: 12345'))
        self.git('add','.'); self.git('commit','-qm','new vendor source')
        head=self.git('rev-parse','HEAD')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_third_party_download_action_legacy_is_grandfathered(self):
        base=self.commit(
            SAFE.replace('actions/download-artifact@','dawidd6/action-download-artifact@')
                .replace('name: evidence','run_id: 12345')
        )
        head=self.commit(
            SAFE.replace('actions/download-artifact@','dawidd6/action-download-artifact@')
                .replace('name: evidence','run_id: 12345')
            + '\n# unrelated end note\n'
        )
        self.assertTrue(module.evaluate(self.repo,base,head)['ok'])

    def test_third_party_quoted_yaml_alias_is_denied(self):
        candidate='      - uses: "dawidd6/action-download-artifact@*alias"'
        with self.assertRaises(module.Refusal):
            module._risks(candidate,'w.yml')

    def test_direct_rest_artifact_zip_url_is_review_debt(self):
        source='      run: curl -fsSL https://api.github.com/repos/example/repo/actions/artifacts/123/zip'
        self.assertEqual(sum(module._risks(source,'w.yml').values()),1)

    def test_dynamic_rest_artifact_route_requires_review(self):
        source='      run: gh api "repos/example/repo/actions/artifacts/' + chr(36) + '{{ steps.old.outputs.id }}/zip"'
        self.assertEqual(sum(module._risks(source,'w.yml').values()),1)

    def test_unrelated_github_api_url_is_not_artifact_debt(self):
        self.assertEqual(sum(module._risks('      run: gh api repos/example/repo/actions/runs/123','w.yml').values()),0)

    def test_rest_api_new_in_real_git_workflow_is_denied(self):
        base=self.commit(SAFE)
        head=self.commit(SAFE + '\n      - run: curl https://api.github.com/repos/example/repo/actions/artifacts/123/zip\n')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_rest_api_unchanged_legacy_is_not_new_debt(self):
        legacy=SAFE + '\n      - run: gh api repos/example/repo/actions/artifacts/123/zip\n'
        base=self.commit(legacy)
        head=self.commit(legacy+'# added documentation\n')
        result=module.evaluate(self.repo,base,head)
        self.assertTrue(result['ok'])
        self.assertEqual(result['legacy_findings'],1)

    def test_rest_api_nested_composite_action_is_denied(self):
        base=self.commit(SAFE)
        manifest=self.repo/'thirdparty/cli/action.yaml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('runs:\n  using: composite\n  steps:\n    - run: gh api repos/example/repo/actions/artifacts/123/zip\n      shell: bash\n')
        self.git('add','.'); self.git('commit','-qm','new composite REST source')
        head=self.git('rev-parse','HEAD')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_rest_api_full_line_comment_does_not_create_debt(self):
        self.assertEqual(sum(module._risks('    # gh api repos/example/repo/actions/artifacts/123/zip','w.yml').values()),0)

    def test_external_artifact_defaults_no_explicit_selector_is_risk(self):
        for action in ('dawidd6/action-download-artifact','step-security/action-download-artifact'):
            with self.subTest(action=action):
                source=SAFE.replace('actions/download-artifact@',action+'@')
                self.assertEqual(sum(module._risks(source,'w.yml').values()),1)

    def test_external_artifact_defaults_no_with_mapping_is_risk(self):
        for action in ('dawidd6/action-download-artifact','step-security/action-download-artifact'):
            with self.subTest(action=action):
                source=SAFE.replace('actions/download-artifact@',action+'@')
                source=source.replace('        with:\n          name: evidence\n','')
                self.assertEqual(sum(module._risks(source,'w.yml').values()),1)

    def test_external_artifact_default_is_denied_from_real_git(self):
        base=self.commit(SAFE)
        head=self.commit(SAFE.replace('actions/download-artifact@','step-security/action-download-artifact@'))
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_external_artifact_default_in_nested_manifest_is_denied(self):
        base=self.commit(SAFE)
        action=self.repo/'actions/download/action.yml'
        action.parent.mkdir(parents=True)
        action.write_text(SAFE.replace('actions/download-artifact@','step-security/action-download-artifact@'))
        self.git('add','.'); self.git('commit','-qm','add implicit cross-run source')
        self.assertEqual(module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))['new_findings'],1)

    def test_external_artifact_default_old_same_source_is_inventory_only(self):
        old=SAFE.replace('actions/download-artifact@','step-security/action-download-artifact@')
        base=self.commit(old)
        head=self.commit(old+'\n# harmless note after step\n')
        output=module.evaluate(self.repo,base,head)
        self.assertTrue(output['ok'])
        self.assertEqual(output['legacy_findings'],1)
        self.assertEqual(output['candidate_findings'],1)

    def test_external_artifact_default_copied_into_second_step_is_denied(self):
        old=SAFE.replace('actions/download-artifact@','step-security/action-download-artifact@')
        base=self.commit(old)
        head=self.commit(old+old[old.index('      - name:'):])
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],1)

    def test_external_artifact_default_action_can_be_removed(self):
        old=SAFE.replace('actions/download-artifact@','step-security/action-download-artifact@')
        base=self.commit(old)
        head=self.commit(SAFE)
        self.assertEqual(module.evaluate(self.repo,base,head)['new_findings'],0)

    def test_external_artifact_default_case_variants_are_risky(self):
        candidate=SAFE.replace('actions/download-artifact@','STEP-SECURITY/ACTION-DOWNLOAD-ARTIFACT@')
        self.assertEqual(sum(module._risks(candidate,'w.yml').values()),1)

    def test_step_security_action_with_unknown_uses_syntax_is_refused(self):
        with self.assertRaises(module.Refusal):
            module._risks('    uses: "*step-security/action-download-artifact@abc"', 'w.yml')

    def test_official_current_run_default_remains_safe(self):
        self.assertEqual(sum(module._risks(SAFE,'w.yml').values()),0)

    def test_new_findings_report_paths_not_secret_values(self):
        base=self.commit(SAFE)
        canary=CROSS.replace('github.token','SENSITIVE_CANARY_7X')
        self.assertIn('SENSITIVE_CANARY_7X',canary)
        head=self.commit(canary)
        report=module.evaluate(self.repo,base,head)
        self.assertEqual(report['new_finding_paths'],['.github/workflows/sample.yml'])
        self.assertNotIn('SENSITIVE_CANARY_7X',str(report))
        self.assertFalse(report['ok'])

    def test_safe_snapshot_has_no_new_finding_paths(self):
        base=self.commit(SAFE)
        head=self.commit(SAFE+'\n# harmless post-step note\n')
        self.assertEqual(module.evaluate(self.repo,base,head)['new_finding_paths'],[])

    def test_nested_composite_reports_exact_path_not_action_values(self):
        base=self.commit(SAFE)
        manifest=self.repo/'actions/tools/action.yml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('runs:\n  using: composite\n  steps:\n    - run: gh run download 12345\n      shell: bash\n')
        self.git('add','.'); self.git('commit','-qm','nested downloader')
        report=module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))
        self.assertEqual(report['new_finding_paths'],['actions/tools/action.yml'])

    def test_two_new_risks_in_one_file_emit_single_path(self):
        base=self.commit(SAFE)
        head=self.commit(CROSS+'\n      - run: gh run download 12345\n')
        report=module.evaluate(self.repo,base,head)
        self.assertEqual(report['new_findings'],2)
        self.assertEqual(report['new_finding_paths'],['.github/workflows/sample.yml'])

    def test_path_with_colon_roundtrips_in_safe_remediation_report(self):
        base=self.commit(SAFE)
        colon=self.repo/'.github/workflows/group:variant.yaml'
        colon.write_text(CROSS)
        self.git('add','.'); self.git('commit','-qm','artifact debt in colon path')
        report=module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))
        self.assertEqual(report['new_finding_paths'],['.github/workflows/group:variant.yaml'])

    def test_gh_and_api_kind_path_helpers_allow_colons(self):
        for kind in ('gh-run-download','github-api-artifact-path'):
            self.assertEqual(module._risk_source_path('a:b/c.yml:44:'+kind+':hash'),'a:b/c.yml')
        self.assertEqual(module._risk_source_path('a:b/c.yml:44:hash'),'a:b/c.yml')

    def test_shell_helper_new_cli_download_denied_by_real_git(self):
        base=self.commit(SAFE)
        sh=self.repo/'scripts/prepare-download.sh'
        sh.parent.mkdir(exist_ok=True)
        sh.write_text('#!/bin/bash\nset -euo pipefail\ngh run download 12345\n')
        self.git('add','.'); self.git('commit','-qm','new shell downloader')
        report=module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))
        self.assertEqual(report['new_findings'],1)
        self.assertEqual(report['new_finding_paths'],['scripts/prepare-download.sh'])

    def test_shell_bash_helper_new_rest_artifact_denied(self):
        base=self.commit(SAFE)
        sh=self.repo/'scripts/artifact-fetch.bash'
        sh.parent.mkdir(exist_ok=True)
        sh.write_text('curl https://api.github.com/repos/example/repo/actions/artifacts/123/zip\n')
        self.git('add','.'); self.git('commit','-qm','new REST script')
        report=module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))
        self.assertEqual(report['new_findings'],1)

    def test_shell_unchanged_legacy_cli_is_inventory_only(self):
        helper=self.repo/'scripts/existing.sh'
        helper.parent.mkdir(exist_ok=True)
        helper.write_text('gh run download 12345\n')
        base=self.commit(SAFE)
        head=self.commit(SAFE+'\n# harmless note after current workflow\n')
        report=module.evaluate(self.repo,base,head)
        self.assertTrue(report['ok'])
        self.assertEqual(report['legacy_findings'],1)
        self.assertEqual(report['new_findings'],0)

    def test_shell_copy_of_legacy_cli_download_denied(self):
        src=self.repo/'scripts/existing.sh'
        src.parent.mkdir(exist_ok=True)
        src.write_text('gh run download 12345\n')
        base=self.commit(SAFE)
        copied=self.repo/'other/copy.sh'
        copied.parent.mkdir(exist_ok=True)
        copied.write_text('gh run download 12345\n')
        self.git('add','.'); self.git('commit','-qm','copy shell downloader')
        self.assertEqual(module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))['new_findings'],1)

    def test_safe_shell_helper_is_permitted(self):
        base=self.commit(SAFE)
        helper=self.repo/'scripts/format.sh'
        helper.parent.mkdir(exist_ok=True)
        helper.write_text('#!/bin/sh\necho "no artifact commands"\n')
        self.git('add','.'); self.git('commit','-qm','safe helper')
        report=module.evaluate(self.repo,base,self.git('rev-parse','HEAD'))
        self.assertTrue(report['ok'])
        self.assertEqual(report['new_findings'],0)

    def test_shell_helper_comment_is_not_executable_risk(self):
        script='#!/bin/bash\n# gh run download 12345\n# curl /actions/artifacts/123/zip\necho ok\n'
        self.assertEqual(sum(module._risks(script,'scripts/example.sh').values()),0)

    def test_shell_non_artifact_github_cli_is_safe(self):
        script='#!/bin/bash\ngh run list --limit 10\ngh api repos/example/repo/actions/runs/2\n'
        self.assertEqual(sum(module._risks(script,'scripts/example.sh').values()),0)

    def test_shell_github_action_literal_is_not_yaml_uses_mapping(self):
        script='#!/bin/sh\necho actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093\n'
        self.assertEqual(sum(module._risks(script,'scripts/example.sh').values()),0)

    def test_named_step_with_input(self):
        self.assertEqual(sum(module._risks(CROSS,'w.yml').values()),1)

    def test_safe_download_has_no_risks(self):
        self.assertEqual(sum(module._risks(SAFE,'w.yml').values()),0)

    def test_comment_does_not_add_risk(self):
        self.assertEqual(sum(module._risks('# actions/download-artifact@foo\n'+SAFE,'w.yml').values()),0)

    def test_rejects_dynamic_action_syntax(self):
        with self.assertRaises(module.Refusal):
            module._risks('    uses: "actions/download-artifact@'+chr(36)+'{{ env.SHA }}"', 'w.yml')

    def test_rejects_duplicate_input(self):
        with self.assertRaises(module.Refusal):
            module._risks(CROSS+'          run-id: 5678\n','w.yml')

if __name__ == '__main__':
    unittest.main()
