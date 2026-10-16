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
