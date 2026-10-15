"""Adversarial regression for the read-only zCloud seven-writer lock inventory."""
import importlib.util
import hashlib
import json
import os
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_deployops_writer_lock_source_20261015_w3.py"
spec = importlib.util.spec_from_file_location("deployops_w3_lock_source", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def workflow(job, scope):
    header = "name: Synthetic\non:\n  workflow_dispatch:\n"
    if scope == "workflow":
        header += "concurrency:\n  group: zcloud-production-deploy\n  cancel-in-progress: false\n"
    text = header + "jobs:\n  test:\n    runs-on: ubuntu-latest\n  " + job + ":\n"
    if scope == "job":
        text += ("    concurrency:\n"
                 "      group: zcloud-production-deploy\n"
                 "      cancel-in-progress: false\n")
    return text + "    runs-on: [self-hosted, zcloud, vps]\n"


class WriterLockSourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dir = self.root / ".github/workflows"
        self.dir.mkdir(parents=True)
        for name, (job, scope) in module.WRITERS.items():
            (self.dir / name).write_text(workflow(job, scope), encoding="utf-8")

    def edit(self, filename, old, new):
        p = self.dir / filename
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new), encoding="utf-8")

    def test_all_seven_serialized_is_source_only_not_release_authority(self):
        output = module.inventory(self.root)
        self.assertEqual(output["total_writers"], 7)
        self.assertEqual(output["source_serialized"], 7)
        self.assertTrue(output["source_lock_complete"])
        self.assertEqual([], output["source_unverified"])
        for field in ("runtime_lock_verified", "release_authorized",
                      "dispatch_authorized", "mutation_performed"):
            self.assertFalse(output[field])

    def test_snapshot_repeated_read_is_deterministic_and_complete(self):
        first = module.inventory(self.root)
        second = module.inventory(self.root)
        self.assertEqual(first["source_snapshot_sha256"], second["source_snapshot_sha256"])
        self.assertTrue(first["source_snapshot_complete"])
        self.assertEqual(64, len(first["source_snapshot_sha256"]))
        for name in module.WRITERS:
            expected = hashlib.sha256((self.dir / name).read_bytes()).hexdigest()
            self.assertEqual(first["writers"][name]["source_sha256"], expected)
        self.assertFalse(first["release_authorized"])

    def test_single_source_byte_change_changes_only_its_file_digest(self):
        first = module.inventory(self.root)
        name = "zcloud-worker-tab-guard.yml"
        path = self.dir / name
        path.write_text(path.read_text(encoding="utf-8") + "# trace-only\n",
                        encoding="utf-8")
        second = module.inventory(self.root)
        self.assertNotEqual(first["source_snapshot_sha256"],
                            second["source_snapshot_sha256"])
        self.assertEqual(first["source_serialized"], second["source_serialized"])
        self.assertTrue(second["source_snapshot_complete"])
        self.assertNotEqual(first["writers"][name]["source_sha256"],
                            second["writers"][name]["source_sha256"])
        for other in module.WRITERS:
            if other != name:
                self.assertEqual(first["writers"][other]["source_sha256"],
                                 second["writers"][other]["source_sha256"])

    def test_missing_file_never_produces_complete_snapshot(self):
        (self.dir / "zcloud-worker-tab-guard.yml").unlink()
        report = module.inventory(self.root)
        self.assertFalse(report["source_snapshot_complete"])
        self.assertIsNone(report["source_snapshot_sha256"])
        self.assertIsNone(report["writers"]["zcloud-worker-tab-guard.yml"]["source_sha256"])
        self.assertFalse(report["release_authorized"])

    def test_unparseable_regular_source_is_hashed_but_not_accepted(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "      group: zcloud-production-deploy",
                  "      group: unsafe-group")
        report = module.inventory(self.root)
        self.assertTrue(report["source_snapshot_complete"])
        self.assertIsNotNone(report["source_snapshot_sha256"])
        self.assertEqual(report["writers"][name]["source_result"], "wrong_group")
        self.assertFalse(report["source_lock_complete"])
        self.assertFalse(report["dispatch_authorized"])

    def test_rejected_binary_file_never_gets_a_sha(self):
        name = "zcloud-worker-tab-guard.yml"
        (self.dir / name).write_bytes(b"invalid:\x00\n")
        report = module.inventory(self.root)
        self.assertFalse(report["source_snapshot_complete"])
        self.assertIsNone(report["source_snapshot_sha256"])
        self.assertIsNone(report["writers"][name]["source_sha256"])

    def test_classification_uses_first_pass_and_rechecks_every_source(self):
        counter = []
        original = module._read_regular

        def tracked_read(root, name):
            counter.append(name)
            return original(root, name)

        with patch.object(module, "_read_regular", side_effect=tracked_read):
            report = module.inventory(self.root)
        self.assertEqual(counter[:7], list(module.WRITERS))
        self.assertEqual(counter[7:], list(module.WRITERS))
        self.assertEqual(report["source_serialized"], 7)
        self.assertTrue(report["source_snapshot_complete"])

    def test_cross_file_change_between_passes_denies_safe_receipt(self):
        """A source modified after its first read must never count as locked."""
        original = module._read_regular
        counter = {}
        target = "zcloud-postsend-tab-preservation-live.yml"

        def changing_read(root, name):
            counter[name] = counter.get(name, 0) + 1
            source = original(root, name)
            if name == target and counter[name] == 2:
                return source + "# changed between read passes\\n"
            return source

        with patch.object(module, "_read_regular", side_effect=changing_read):
            report = module.inventory(self.root)
        self.assertEqual(report["writers"][target]["source_result"],
                         "unstable_source_snapshot")
        self.assertIsNone(report["writers"][target]["source_sha256"])
        self.assertFalse(report["source_snapshot_complete"])
        self.assertIsNone(report["source_snapshot_sha256"])
        self.assertFalse(report["source_lock_complete"])
        self.assertEqual(report["source_serialized"], 6)
        self.assertFalse(report["release_authorized"])
        self.assertEqual(len(counter), 7)

    def test_unreadable_file_on_second_pass_denies_receipt(self):
        original = module._read_regular
        target = "zcloud-safe-memory-reclaim-20261013.yml"
        observed = []

        def disappearing_read(root, name):
            observed.append(name)
            if name == target and observed.count(name) == 2:
                raise module.AmbiguousSource("unreadable_source")
            return original(root, name)

        with patch.object(module, "_read_regular", side_effect=disappearing_read):
            report = module.inventory(self.root)
        self.assertEqual(report["writers"][target]["source_result"],
                         "unstable_source_snapshot")
        self.assertIsNone(report["writers"][target]["source_sha256"])
        self.assertFalse(report["source_snapshot_complete"])
        self.assertEqual(report["source_serialized"], 6)
        self.assertFalse(report["mutation_performed"])

    def test_stable_second_pass_does_not_change_source_hash_or_authority(self):
        expected = module.inventory(self.root)
        actual = module.inventory(self.root)
        self.assertEqual(expected["source_snapshot_sha256"],
                         actual["source_snapshot_sha256"])
        self.assertEqual(expected["writers"], actual["writers"])
        self.assertTrue(actual["source_snapshot_complete"])
        self.assertFalse(actual["runtime_lock_verified"])

    def test_expected_source_snapshot_cli_matching_digest_succeeds(self):
        digest = module.inventory(self.root)["source_snapshot_sha256"]
        args = [sys.executable, str(SCRIPT), "--root", str(self.root),
                "--expect-source-snapshot-sha256", digest]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["source_snapshot_matches_expected"])
        self.assertFalse(report["release_authorized"])

    def test_expected_source_snapshot_cli_detects_single_comment_drift(self):
        digest = module.inventory(self.root)["source_snapshot_sha256"]
        path = self.dir / "zcloud-worker-tab-guard.yml"
        path.write_text(path.read_text(encoding="utf-8") + "# drift\n",
                        encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root),
             "--expect-source-snapshot-sha256", digest],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 2)
        report = json.loads(proc.stdout)
        self.assertFalse(report["source_snapshot_matches_expected"])
        self.assertTrue(report["source_snapshot_complete"])
        self.assertFalse(report["mutation_performed"])

    def test_expected_source_snapshot_with_missing_file_denies(self):
        digest = module.inventory(self.root)["source_snapshot_sha256"]
        (self.dir / "zcloud-worker-tab-guard.yml").unlink()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root),
             "--expect-source-snapshot-sha256", digest],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 2)
        report = json.loads(proc.stdout)
        self.assertFalse(report["source_snapshot_matches_expected"])
        self.assertIsNone(report["source_snapshot_sha256"])

    def test_expected_source_snapshot_rejects_ambiguous_invalid_inputs(self):
        digest = module.inventory(self.root)["source_snapshot_sha256"]
        for value in ("", digest.upper(), digest[:-1], digest + "0",
                      digest + "\n", "x" * 64, "0" * 65):
            with self.subTest(value=value[:15]):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), "--root", str(self.root),
                     "--expect-source-snapshot-sha256", value],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertFalse(proc.stdout)

    def test_expected_source_snapshot_does_not_override_lock_denial(self):
        digest = module.inventory(self.root)["source_snapshot_sha256"]
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "      group: zcloud-production-deploy",
                  "      group: unsafe-group")
        matching = module.inventory(self.root)["source_snapshot_sha256"]
        self.assertNotEqual(digest, matching)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root),
             "--expect-source-snapshot-sha256", matching, "--expect-serialized"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertTrue(report["source_snapshot_matches_expected"])
        self.assertFalse(report["source_lock_complete"])
        self.assertFalse(report["release_authorized"])

    def test_known_existing_three_debts_have_independent_job_scopes(self):
        for name in ("zcloud-worker-tab-guard.yml",
                     "zcloud-no-browser-churn-hotfix.yml",
                     "zcloud-emergency-addon-hot-reload.yml"):
            with self.subTest(name=name):
                self.edit(name, "      group: zcloud-production-deploy\n", "")
                evidence = module.inventory(self.root)
                self.assertIn(name, evidence["source_unverified"])
                self.assertFalse(evidence["dispatch_authorized"])
                self.edit(name, "    concurrency:\n", "    concurrency:\n      group: zcloud-production-deploy\n")

    def test_workflow_level_shared_production_lock_cannot_replace_job_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    concurrency:\n      group: zcloud-production-deploy\n      cancel-in-progress: false\n", "")
        p = self.dir / name
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("jobs:", "concurrency:\n  group: zcloud-production-deploy\n  cancel-in-progress: false\njobs:"), encoding="utf-8")
        self.assertEqual(module.inspect_workflow(self.root, name, "install", "job"),
                         "parent_shares_production_group")

    def test_missing_job_lock_is_rejected_even_with_disjoint_parent(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    concurrency:\n      group: zcloud-production-deploy\n      cancel-in-progress: false\n", "")
        self.edit(name, "jobs:\n", "concurrency:\n  group: separate-hosted-workflow\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inspect_workflow(self.root, name, "install", "job"),
                         "missing_concurrency")

    def test_workflow_level_true_cancel_defeats_safe_job_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n", "concurrency:\n  group: only-manual-group\n  cancel-in-progress: true\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsafe_parent_cancel_policy")
        self.assertFalse(module.inventory(self.root)["release_authorized"])

    def test_parent_cancel_expression_must_not_be_trusted(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n", "concurrency:\n  group: only-manual-group\n  cancel-in-progress: ${{ github.event_name == 'push' }}\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsafe_parent_cancel_policy")

    def test_parent_non_cancel_group_does_not_replace_job_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n", "concurrency:\n  group: manual-only-group\n  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "serialized_source")
        self.edit(name, "      group: zcloud-production-deploy\n", "")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "wrong_group")

    def test_parent_same_shared_production_group_is_not_independent(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: zcloud-production-deploy\n"
                  "  cancel-in-progress: false\njobs:\n")
        report = module.inventory(self.root)
        self.assertEqual(report["writers"][name]["source_result"],
                         "parent_shares_production_group")
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["dispatch_authorized"])

    def test_parent_expression_only_is_ambiguous(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: ${{ vars.DEPLOY_LOCK }}\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_expression_completing_production_group_is_ambiguous(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: zcloud-production-deplo${{ vars.SUFFIX }}\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_separate_static_prefix_expression_keeps_safe_job_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: zcloud-hosted-${{ github.ref }}\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "serialized_source")

    def test_parent_missing_group_is_ambiguous(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_shared_lock_differs_only_by_case(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: ZCLOUD-PRODUCTION-DEPLOY\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "parent_shares_production_group")

    def test_parent_shared_lock_with_trailing_spaces_refused(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: zcloud-production-deploy   \n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "parent_shares_production_group")

    def test_parent_expression_case_insensitive_partial_prefix_refused(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: ZCLOUD-PRODUCTION-DEPL${{ vars.REST }}\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_inline_comment_cannot_hide_duplicate_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: zcloud-production-deploy # hidden shadow\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_quoted_group_cannot_hide_same_production_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  'concurrency:\n  group: "zcloud-production-deploy"\n'
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_single_quoted_group_cannot_hide_same_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: 'ZCLOUD-PRODUCTION-DEPLOY'\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_folded_scalar_group_refused(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: >-\n    zcloud-production-deploy\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_alias_group_refused(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: *production_group\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_explicit_yaml_tag_group_refused(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n",
                  "concurrency:\n  group: !!str zcloud-production-deploy\n"
                  "  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_group")

    def test_parent_cancel_after_jobs_is_denied(self):
        name = "zcloud-worker-tab-guard.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as stream:
            stream.write("concurrency:\n  group: zcloud-production-deploy\n"
                         "  cancel-in-progress: true\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsafe_parent_cancel_policy")

    def test_safe_parent_concurrency_after_jobs_is_allowed(self):
        name = "zcloud-worker-tab-guard.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as stream:
            stream.write("concurrency:\n  group: manual-only-group\n"
                         "  cancel-in-progress: false\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "serialized_source")

    def test_root_concurrency_before_and_after_jobs_is_ambiguous(self):
        name = "zcloud-vps-deploy.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as stream:
            stream.write("concurrency:\n  group: unsafe-separate-group\n"
                         "  cancel-in-progress: true\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_concurrency")

    def test_quoted_root_jobs_shadow_is_denied(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n", "jobs:\n\"jobs\":\n",)
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_yaml_mapping_form")

    def test_yaml_unicode_escape_quoted_shadow_key_denied(self):
        name = "zcloud-vps-deploy.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as output:
            output.write('''"\\u006aobs": {}\n''')
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_yaml_mapping_form")

    def test_yaml_explicit_mapping_form_denied(self):
        name = "zcloud-worker-tab-guard.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as output:
            output.write("? jobs\n: {}\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_yaml_mapping_form")

    def test_yaml_merge_key_is_denied(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "      group: zcloud-production-deploy\n",
                  "      <<: *potential_override\n      group: zcloud-production-deploy\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_yaml_mapping_form")

    def test_second_yaml_document_is_denied(self):
        name = "zcloud-vps-deploy.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as output:
            output.write("---\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_yaml_mapping_form")

    def test_job_outside_jobs_block_cannot_supply_missing_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        p = self.dir / name
        text = p.read_text(encoding="utf-8")
        text = text.replace("  install:\n", "  removed:\n", 1)
        text += ("extra:\n  install:\n    concurrency:\n"
                 "      group: zcloud-production-deploy\n"
                 "      cancel-in-progress: false\n")
        p.write_text(text, encoding="utf-8")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "missing_or_duplicate_mutation_job")

    def test_inline_vps_writer_does_not_evade_runner_inventory(self):
        name = "zcloud-worker-tab-guard.yml"
        path = self.dir / name
        with path.open("a", encoding="utf-8") as stream:
            stream.write("  stealth: {runs-on: [self-hosted, zcloud, vps]}\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_job_mapping")
        self.assertFalse(module.inventory(self.root)["source_lock_complete"])

    def test_anchored_job_declaration_does_not_hide_vps_writer(self):
        name = "zcloud-vps-deploy.yml"
        path = self.dir / name
        with path.open("a", encoding="utf-8") as stream:
            stream.write("  stealth: &worker_def\n"
                         "    runs-on: [self-hosted, zcloud, vps]\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_job_mapping")

    def test_list_style_job_declaration_denied(self):
        name = "zcloud-worker-tab-guard.yml"
        path = self.dir / name
        with path.open("a", encoding="utf-8") as stream:
            stream.write("  - unexpected-job\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unsupported_job_mapping")

    def test_extra_self_hosted_writer_cannot_hide_beside_locked_job(self):
        name = "zcloud-worker-tab-guard.yml"
        p = self.dir / name
        with p.open("a", encoding="utf-8") as stream:
            stream.write("  surprise-live-writer:\n"
                         "    runs-on: [self-hosted, zcloud, vps]\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_companion_runner_target")

    def test_extra_self_hosted_job_denied_with_different_label_style(self):
        name = "zcloud-vps-deploy.yml"
        p = self.dir / name
        text = p.read_text(encoding="utf-8")
        text += "  extra:\n    runs-on: self-hosted\n"
        p.write_text(text, encoding="utf-8")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_companion_runner_target")

    def test_missing_target_runner_is_refused_even_if_lock_present(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]\n", "")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "missing_or_duplicate_runner_target")

    def test_target_runner_cannot_be_redirected_to_hosted(self):
        name = "zcloud-vps-deploy.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]",
                  "    runs-on: ubuntu-latest")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_mutation_runner_target")

    def test_duplicate_runner_selection_on_mutation_job_refused(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]",
                  "    runs-on: [self-hosted, zcloud, vps]\n"
                  "    runs-on: ubuntu-latest")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "missing_or_duplicate_runner_target")

    def test_companion_runner_cannot_be_promoted_to_vps(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    runs-on: ubuntu-latest",
                  "    runs-on: [self-hosted, zcloud, vps]")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_companion_runner_target")

    def test_dynamic_runner_expression_fails_closed(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]",
                  "    runs-on: ${{ fromJSON(vars.DEPLOY_RUNNER) }}")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_mutation_runner_target")

    def test_canonical_nested_cancel_true_refused_even_if_outer_false(self):
        name = "zcloud-vps-deploy.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]",
                  "    concurrency:\n      group: another-deploy\n"
                  "      cancel-in-progress: true\n    runs-on: [self-hosted, zcloud, vps]")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_nested_concurrency")

    def test_canonical_nested_identical_group_refused_to_avoid_deadlock(self):
        name = "zcloud-vps-deploy.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]",
                  "    concurrency:\n      group: zcloud-production-deploy\n"
                  "      cancel-in-progress: false\n    runs-on: [self-hosted, zcloud, vps]")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "unexpected_nested_concurrency")

    def test_workflow_concurrency_after_jobs_is_honored_for_canonical(self):
        name = "zcloud-vps-deploy.yml"
        p = self.dir / name
        text = p.read_text(encoding="utf-8")
        text = text.replace("concurrency:\n  group: zcloud-production-deploy\n"
                            "  cancel-in-progress: false\n", "", 1)
        text += ("concurrency:\n  group: zcloud-production-deploy\n"
                 "  cancel-in-progress: false\n")
        p.write_text(text, encoding="utf-8")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "serialized_source")

    def test_duplicate_parent_concurrency_rejected(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "jobs:\n", "concurrency:\n  group: first\n  cancel-in-progress: false\nconcurrency:\n  group: second\n  cancel-in-progress: false\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_parent_concurrency")

    def test_wrong_group_does_not_count(self):
        self.edit("zcloud-worker-tab-guard.yml",
                  "group: zcloud-production-deploy", "group: another-production-lock")
        self.assertEqual(module.inventory(self.root)["source_serialized"], 6)

    def test_dynamic_group_rejected(self):
        self.edit("zcloud-vps-deploy.yml", "group: zcloud-production-deploy",
                  "group: zcloud-production-deploy-${{ github.ref }}")
        self.assertEqual(module.inventory(self.root)["writers"]["zcloud-vps-deploy.yml"]["source_result"],
                         "wrong_group")

    def test_cancel_in_progress_true_rejected(self):
        self.edit("zcloud-no-browser-churn-hotfix.yml", "cancel-in-progress: false",
                  "cancel-in-progress: true")
        self.assertEqual(module.inventory(self.root)["writers"]["zcloud-no-browser-churn-hotfix.yml"]["source_result"],
                         "unsafe_cancel_policy")

    def test_cancel_policy_missing_rejected(self):
        self.edit("zcloud-emergency-addon-hot-reload.yml", "      cancel-in-progress: false\n", "")
        self.assertEqual(module.inventory(self.root)["source_serialized"], 6)

    def test_duplicate_group_is_rejected(self):
        self.edit("zcloud-worker-tab-guard.yml",
                  "      group: zcloud-production-deploy\n",
                  "      group: zcloud-production-deploy\n      group: zcloud-production-deploy\n")
        self.assertEqual(module.inventory(self.root)["writers"]["zcloud-worker-tab-guard.yml"]["source_result"],
                         "duplicate_lock_field")

    def test_duplicate_concurrency_is_rejected(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    runs-on: [self-hosted, zcloud, vps]",
                  "    concurrency:\n      group: zcloud-production-deploy\n      cancel-in-progress: false\n    runs-on: [self-hosted, zcloud, vps]")
        self.assertEqual(module.inventory(self.root)["writers"][name]["source_result"],
                         "ambiguous_concurrency")

    def test_duplicate_jobs_root_is_denied(self):
        self.edit("zcloud-vps-deploy.yml", "jobs:\n", "jobs:\njobs:\n")
        self.assertEqual(module.inventory(self.root)["writers"]["zcloud-vps-deploy.yml"]["source_result"],
                         "ambiguous_jobs")

    def test_missing_target_job_is_denied(self):
        self.edit("zcloud-worker-tab-guard.yml", "  install:", "  another-job:")
        self.assertEqual(module.inventory(self.root)["writers"]["zcloud-worker-tab-guard.yml"]["source_result"],
                         "missing_or_duplicate_mutation_job")

    def test_missing_source_denied(self):
        (self.dir / "zcloud-worker-tab-guard.yml").unlink()
        report = module.inventory(self.root)
        self.assertEqual(report["source_serialized"], 6)
        self.assertFalse(report["source_lock_complete"])

    def test_symlink_leaf_refused(self):
        path = self.dir / "zcloud-worker-tab-guard.yml"
        path.unlink()
        path.symlink_to(self.dir / "zcloud-vps-deploy.yml")
        self.assertEqual(module.inventory(self.root)["writers"][path.name]["source_result"],
                         "symlink_source")

    def test_symlink_parent_refused(self):
        target = self.root / "realworkflows"
        self.dir.rename(target)
        self.dir.symlink_to(target, target_is_directory=True)
        self.assertEqual(module.inventory(self.root)["source_serialized"], 0)

    def test_symlink_root_ancestor_refused(self):
        alias = self.root.parent / (self.root.name + "-source-alias")
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        evidence = module.inventory(alias)
        self.assertEqual(evidence["source_serialized"], 0)
        self.assertTrue(all(item["source_result"] == "symlink_source"
                            for item in evidence["writers"].values()))

    def test_no_follow_directory_reads_normal_regular_files(self):
        # The same inspector works with dirfd-anchored reads on an ordinary tree.
        for filename, (job, scope) in module.WRITERS.items():
            with self.subTest(filename=filename):
                self.assertEqual(module.inspect_workflow(self.root, filename, job, scope),
                                 "serialized_source")

    def test_nonregular_fifo_never_hangs_or_counts_as_safe(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFO required")
        path = self.dir / "zcloud-worker-tab-guard.yml"
        path.unlink()
        os.mkfifo(path)
        # Run in a child with an explicit 2s timeout: malicious FIFO must
        # never wait forever at open(), even without a writer.
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)],
            text=True, capture_output=True, timeout=2, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["writers"][path.name]["source_result"],
                         "not_bounded_regular_source")

    def test_short_source_read_is_denied_instead_of_partial_success(self):
        # A short read on a regular file is legal; never accept just an
        # apparent safe YAML prefix when the remainder is unobserved.
        with patch.object(module.os, "read", return_value=b""):
            evidence = module.inventory(self.root)
        self.assertEqual(evidence["source_serialized"], 0)
        self.assertEqual(evidence["writers"]["zcloud-vps-deploy.yml"]["source_result"],
                         "source_changed_during_read")

    def test_partial_reads_continue_until_complete(self):
        actual_read = module.os.read

        def short_read(fd, length):
            return actual_read(fd, min(length, 7))

        with patch.object(module.os, "read", side_effect=short_read):
            evidence = module.inventory(self.root)
        self.assertEqual(evidence["source_serialized"], 7)

    def test_oversized_file_rejected(self):
        path = self.dir / "zcloud-worker-tab-guard.yml"
        path.write_text("x" * (module.MAX_BYTES + 1), encoding="utf-8")
        self.assertEqual(module.inventory(self.root)["writers"][path.name]["source_result"],
                         "not_bounded_regular_source")

    def test_binary_source_rejected(self):
        path = self.dir / "zcloud-worker-tab-guard.yml"
        path.write_bytes(b"jobs:\x00\n")
        self.assertEqual(module.inventory(self.root)["writers"][path.name]["source_result"],
                         "invalid_source_bytes")

    def test_tabs_are_not_accepted_as_yaml_evidence(self):
        self.edit("zcloud-worker-tab-guard.yml", "  install:", "\tinstall:")
        self.assertEqual(module.inventory(self.root)["writers"]["zcloud-worker-tab-guard.yml"]["source_result"],
                         "tab_indentation_or_scalar")

    def test_cli_reports_current_data_and_strict_mode(self):
        args = [sys.executable, str(SCRIPT), "--root", str(self.root)]
        good = subprocess.run(args + ["--expect-serialized"], text=True, capture_output=True, check=False)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)["source_lock_complete"])
        self.edit("zcloud-worker-tab-guard.yml", "      group: zcloud-production-deploy\n", "")
        pending = subprocess.run(args, text=True, capture_output=True, check=False)
        strict = subprocess.run(args + ["--expect-serialized"], text=True, capture_output=True, check=False)
        self.assertEqual(pending.returncode, 0)
        self.assertEqual(strict.returncode, 1)
        self.assertFalse(json.loads(strict.stdout)["release_authorized"])

    def test_actual_repository_is_report_only_never_mutating(self):
        report = module.inventory(ROOT)
        self.assertEqual(set(report["writers"]), set(module.WRITERS))
        self.assertEqual(report["total_writers"], 7)
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["mutation_performed"])


if __name__ == "__main__":
    unittest.main()
