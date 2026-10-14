"""Offline regression for cross-workflow zCloud production writer locking.

The audit deliberately keeps known unremediated workflows visible in its
non-authorizing report. None of the tests invoke GitHub Actions or VPS APIs.
"""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/zcloud_production_writer_lock_inventory_20261014_w9.py"
SPEC = importlib.util.spec_from_file_location("release_writer_inventory", SOURCE)
assert SPEC and SPEC.loader
audit_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_mod)


def text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


class ProductionWriterInventoryTests(unittest.TestCase):
    def test_actual_main_source_has_protected_writers_and_explicit_existing_debt(self):
        result = audit_mod.audit(ROOT)
        self.assertEqual(result["policy"], "zcloud-production-writer-lock-inventory-v1")
        self.assertEqual(result["observed_workflows"], 7)
        self.assertEqual(len(result["protected_workflows"]), 4)
        self.assertEqual(len(result["outstanding_writer_debt"]), 3)
        self.assertEqual(result["new_or_regressed_debt"], [])
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["production_verified"])
        self.assertFalse(result["release_authorized"])

    def test_protected_writer_jobs_share_one_exact_lock(self):
        for workflow in audit_mod.PROTECTED:
            with self.subTest(workflow=workflow):
                outcome = audit_mod.inspect_source(workflow, text(workflow))
                self.assertEqual(outcome["risk"], "protected")

    def test_known_debt_is_not_misreported_as_success(self):
        for workflow in audit_mod.KNOWN_DEBT:
            with self.subTest(workflow=workflow):
                outcome = audit_mod.inspect_source(workflow, text(workflow))
                self.assertNotIn(outcome["risk"], ("protected", "remediated"))
                self.assertIn(workflow, audit_mod.audit(ROOT)["outstanding_writer_debt"])

    def test_missing_lock_in_protected_writer_is_detected(self):
        for workflow, (_, location) in audit_mod.PROTECTED.items():
            with self.subTest(workflow=workflow):
                current = text(workflow)
                self.assertIn("group: zcloud-production-deploy", current)
                modified = current.replace("group: zcloud-production-deploy",
                                           "group: unrelated-release-lock", 1)
                self.assertNotEqual(
                    audit_mod.inspect_source(workflow, modified)["risk"], "protected")

    def test_write_cancellation_is_not_accepted(self):
        for workflow in audit_mod.PROTECTED:
            with self.subTest(workflow=workflow):
                current = text(workflow)
                if "cancel-in-progress: false" not in current:
                    self.fail(f"No expected non-cancelling writer in {workflow}")
                changed = current.replace("cancel-in-progress: false",
                                          "cancel-in-progress: true", 1)
                self.assertNotEqual(
                    audit_mod.inspect_source(workflow, changed)["risk"], "protected")

    def test_root_hosted_proof_cannot_take_live_writer_lock(self):
        name = "zcloud-postsend-tab-preservation-live.yml"
        current = text(name)
        self.assertIn("group: zcloud-postsend-hosted-", current)
        changed = current.replace("group: zcloud-postsend-hosted-",
                                  "group: zcloud-production-deploy", 1)
        self.assertEqual("hosted_proof_holds_writer_lock",
                         audit_mod.inspect_source(name, changed)["risk"])

    def test_existing_debt_cannot_gain_automatic_live_mutation(self):
        for workflow in audit_mod.KNOWN_DEBT:
            with self.subTest(workflow=workflow):
                current = text(workflow)
                changed = current.replace(
                    "github.event_name == 'workflow_dispatch'",
                    "github.event_name == 'push'", 1)
                self.assertEqual("auto_live_entrypoint_added",
                                 audit_mod.inspect_source(workflow, changed)["risk"])

    def test_known_debt_can_be_remediated_without_failing_no_new_debt_policy(self):
        name = "zcloud-emergency-addon-hot-reload.yml"
        current = text(name)
        assert "  activate:\n" in current
        remediated = current.replace("    runs-on: [self-hosted, zcloud, vps]",
                                     "    concurrency:\n"
                                     "      group: zcloud-production-deploy\n"
                                     "      cancel-in-progress: false\n"
                                     "    runs-on: [self-hosted, zcloud, vps]", 1)
        self.assertEqual("remediated", audit_mod.inspect_source(name, remediated)["risk"])

    def test_duplicate_or_missing_workflow_roots_fail_closed(self):
        name = "zcloud-vps-deploy.yml"
        source = text(name)
        for changed in (source + "\njobs:\n  shadow:\n",
                        source + "\non:\n  workflow_dispatch:\n",
                        source.replace("\njobs:\n", "\nnot_jobs:\n", 1)):
            with self.subTest(variant=len(changed)):
                with self.assertRaises(audit_mod.InvalidSource):
                    audit_mod.inspect_source(name, changed)

    def test_missing_protected_source_is_reported_new_debt(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / ".github" / "workflows"
            dest.mkdir(parents=True)
            for name in audit_mod.WATCHED:
                if name != "zcloud-vps-deploy.yml":
                    (dest / name).write_text(text(name), encoding="utf-8")
            result = audit_mod.audit(Path(directory))
            self.assertIn("zcloud-vps-deploy.yml", result["new_or_regressed_debt"])
            self.assertFalse(result["release_authorized"])

    def test_offline_cli_strict_denies_regressed_production_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / ".github" / "workflows"
            dest.mkdir(parents=True)
            for name in audit_mod.WATCHED:
                (dest / name).write_text(text(name), encoding="utf-8")
            command = [sys.executable, str(SOURCE), "--repo-root", str(directory),
                       "--deny-new-debt"]
            ok = subprocess.run(command, capture_output=True, text=True, timeout=4)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout)["new_or_regressed_debt"], [])
            name = "zcloud-safe-memory-reclaim-20261013.yml"
            (dest / name).write_text(text(name).replace(
                "group: zcloud-production-deploy", "group: divergent-writer"), encoding="utf-8")
            denied = subprocess.run(command, capture_output=True, text=True, timeout=4)
            self.assertEqual(denied.returncode, 2, denied.stderr)
            self.assertIn(name, json.loads(denied.stdout)["new_or_regressed_debt"])

    def test_source_inventory_never_grants_authorization(self):
        report = audit_mod.audit(ROOT)
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["production_verified"])
        self.assertFalse(report["mutation_performed"])
        self.assertTrue(report["source_only"])
        self.assertNotIn("sqlite3", SOURCE.read_text(encoding="utf-8"))
        self.assertNotIn("urllib.request", SOURCE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
