#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_metadata_remediation_plan.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-metadata-remediation-plan.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_metadata_plan", SCRIPT)
assert SPEC and SPEC.loader
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

MAIN = "1" * 40


def audit_report(stale=None):
    stale = list(stale or [])
    return {
        "schema": "zcloud-serialized-gate-metadata-drift-audit-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "status": "drift_detected" if stale else "clean",
        "primary_gate": 580,
        "retired_gate": 576,
        "successor_gate": 1089,
        "successor_open": True,
        "stale_reference_count": len(stale),
        "stale_reference_prs": stale,
        "mutation_performed": False,
        "release_authorized": False,
    }


class SerializedGateMetadataRemediationPlanTests(unittest.TestCase):
    def test_batches_are_sorted_bounded_and_deterministic(self):
        result = plan.plan_remediation(
            audit_report([9, 2, 7, 1, 8, 6, 5, 4, 3]),
            batch_size=4,
        )
        self.assertEqual("remediation_needed", result["status"])
        self.assertEqual(
            [
                {"batch_index": 1, "count": 4, "pr_numbers": [1, 2, 3, 4]},
                {"batch_index": 2, "count": 4, "pr_numbers": [5, 6, 7, 8]},
                {"batch_index": 3, "count": 1, "pr_numbers": [9]},
            ],
            result["batches"],
        )
        self.assertFalse(result["metadata_write_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])
        self.assertTrue(result["requires_fresh_revalidation"])

    def test_clean_report_produces_no_batches(self):
        result = plan.plan_remediation(audit_report([]))
        self.assertEqual("clean", result["status"])
        self.assertEqual(0, result["batch_count"])
        self.assertEqual([], result["batches"])

    def test_duplicate_pr_numbers_fail_closed(self):
        with self.assertRaisesRegex(plan.PlanError, "duplicate stale PR"):
            plan.plan_remediation(audit_report([10, 10]))

    def test_count_and_status_mismatches_fail_closed(self):
        payload = audit_report([10])
        payload["stale_reference_count"] = 2
        with self.assertRaisesRegex(plan.PlanError, "count mismatch"):
            plan.plan_remediation(payload)

        payload = audit_report([10])
        payload["status"] = "clean"
        with self.assertRaisesRegex(plan.PlanError, "clean audit"):
            plan.plan_remediation(payload)

        payload = audit_report([])
        payload["status"] = "drift_detected"
        with self.assertRaisesRegex(plan.PlanError, "has no stale"):
            plan.plan_remediation(payload)

    def test_source_mutation_or_release_authorization_is_rejected(self):
        payload = audit_report([10])
        payload["mutation_performed"] = True
        with self.assertRaisesRegex(plan.PlanError, "mutated"):
            plan.plan_remediation(payload)

        payload = audit_report([10])
        payload["release_authorized"] = True
        with self.assertRaisesRegex(plan.PlanError, "authorized release"):
            plan.plan_remediation(payload)

    def test_successor_must_still_be_open_when_drift_exists(self):
        payload = audit_report([10])
        payload["successor_open"] = False
        with self.assertRaisesRegex(plan.PlanError, "requires refresh"):
            plan.plan_remediation(payload)

    def test_batch_size_is_bounded(self):
        with self.assertRaisesRegex(plan.PlanError, "batch_size"):
            plan.plan_remediation(audit_report([1]), batch_size=0)
        with self.assertRaisesRegex(plan.PlanError, "batch_size"):
            plan.plan_remediation(audit_report([1]), batch_size=51)

    def test_output_does_not_echo_unknown_sensitive_fields(self):
        secret = "do-not-echo-pr-title-or-body"
        payload = audit_report([10])
        payload["body"] = secret
        payload["title"] = secret
        rendered = json.dumps(plan.plan_remediation(payload), sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"title"', rendered)

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "audit.json"
            real.write_text(json.dumps(audit_report([10])), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(plan.PlanError, "symlink"):
                plan._load_json(link)

    def test_cli_emits_read_only_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.json"
            path.write_text(json.dumps(audit_report([3, 1, 2])), encoding="utf-8")
            code = plan.main(
                ["--audit-report", str(path), "--batch-size", "2", "--json"]
            )
        self.assertEqual(0, code)

    def test_workflow_is_read_only_exact_head_and_permanent_vps_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn("stale_reference_prs", text)
        self.assertIn("metadata_write_authorized", text)
        self.assertIn("merge_authorized", text)
        self.assertIn("deploy_authorized", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh pr edit", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("git push", text)


if __name__ == "__main__":
    unittest.main()
