"""Adversarial regression for the read-only zCloud seven-writer lock inventory."""
import importlib.util
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

    def test_job_level_required_even_with_workflow_level_lock(self):
        name = "zcloud-worker-tab-guard.yml"
        self.edit(name, "    concurrency:\n      group: zcloud-production-deploy\n      cancel-in-progress: false\n", "")
        p = self.dir / name
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("jobs:", "concurrency:\n  group: zcloud-production-deploy\n  cancel-in-progress: false\njobs:"), encoding="utf-8")
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
