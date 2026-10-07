#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_batch_plan.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-batch-plan.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_batch", SCRIPT)
assert SPEC and SPEC.loader
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

MAIN = "1" * 40


def backlog(*, released=False, dependents=None):
    dependents = dependents or []
    return {
        "schema": "zcloud-serialized-gate-backlog-audit-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "inventory_complete": True,
        "status": "released_with_backlog" if released else "blocked",
        "gate_open": not released,
        "integration_released": released,
        "gates": [
            {"number": 580, "open": not released},
            {"number": 576, "open": not released},
        ],
        "dependent_pr_count": len(dependents),
        "review_ready_current_main_count": sum(
            1 for item in dependents if item.get("review_ready_current_main")
        ),
        "stale_base_count": sum(
            1 for item in dependents if item.get("base_is_current_main") is False
        ),
        "draft_count": sum(1 for item in dependents if item.get("draft") is True),
        "dependents": dependents,
        "mutation_performed": False,
    }


def dep(number, *, ready=True):
    return {
        "number": number,
        "draft": False if ready else True,
        "head_sha": "2" * 40,
        "base_sha": MAIN,
        "base_is_current_main": True,
        "review_ready_current_main": ready,
    }


def payload(dependents, files, *, released=False):
    return {
        "backlog": backlog(released=released, dependents=dependents),
        "files_by_pr": {
            str(number): {"complete": True, "files": paths}
            for number, paths in files.items()
        },
    }


class SerializedGateBatchPlanTests(unittest.TestCase):
    def test_selects_deterministic_file_disjoint_batch(self):
        data = payload(
            [dep(20), dep(10), dep(30), dep(40)],
            {
                10: ["a.py"],
                20: ["b.py", "c.py"],
                30: ["a.py", "d.py"],
                40: ["e.py"],
            },
        )
        result = plan.build_plan(data, batch_limit=10)
        self.assertEqual("blocked_preview", result["status"])
        self.assertEqual([10, 40, 20], [item["number"] for item in result["selected"]])
        conflict = next(item for item in result["deferred"] if item["number"] == 30)
        self.assertEqual("file_conflict", conflict["reason"])
        self.assertEqual([10], conflict["conflicts_with"])

    def test_batch_limit_is_deterministic(self):
        data = payload(
            [dep(3), dep(1), dep(2)],
            {1: ["a"], 2: ["b"], 3: ["c"]},
        )
        result = plan.build_plan(data, batch_limit=2)
        self.assertEqual([1, 2], [item["number"] for item in result["selected"]])
        self.assertEqual("batch_limit", result["deferred"][0]["reason"])

    def test_gate_open_never_authorizes_merge(self):
        result = plan.build_plan(payload([dep(1)], {1: ["a"]}, released=False))
        self.assertFalse(result["gate_released"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["merge_authorized"])
        self.assertTrue(result["ci_revalidation_required"])
        self.assertTrue(result["ownership_revalidation_required"])

    def test_released_gate_still_requires_revalidation(self):
        result = plan.build_plan(payload([dep(1)], {1: ["a"]}, released=True))
        self.assertEqual("candidate_batch", result["status"])
        self.assertTrue(result["gate_released"])
        self.assertFalse(result["preview_only"])
        self.assertFalse(result["merge_authorized"])
        self.assertTrue(result["ci_revalidation_required"])

    def test_incoherent_gate_release_state_fails_closed(self):
        data = payload([dep(1)], {1: ["a"]}, released=False)
        data["backlog"]["integration_released"] = True
        with self.assertRaisesRegex(plan.PlanError, "incoherent gate release"):
            plan.build_plan(data)

    def test_summary_count_mismatch_fails_closed(self):
        data = payload([dep(1)], {1: ["a"]})
        data["backlog"]["dependent_pr_count"] = 2
        with self.assertRaisesRegex(plan.PlanError, "dependent PR count mismatch"):
            plan.build_plan(data)

    def test_extra_changed_file_evidence_fails_closed(self):
        data = payload([dep(1)], {1: ["a"], 2: ["b"]})
        with self.assertRaisesRegex(plan.PlanError, "key set mismatch"):
            plan.build_plan(data)

    def test_incomplete_changed_file_evidence_fails_closed(self):
        data = payload([dep(1)], {1: ["a"]})
        data["files_by_pr"]["1"]["complete"] = False
        with self.assertRaisesRegex(plan.PlanError, "incomplete changed-file"):
            plan.build_plan(data)

    def test_missing_changed_file_evidence_fails_closed(self):
        data = payload([dep(1)], {})
        with self.assertRaisesRegex(plan.PlanError, "missing changed-file"):
            plan.build_plan(data)

    def test_unsafe_path_fails_closed(self):
        data = payload([dep(1)], {1: ["../server.py"]})
        with self.assertRaisesRegex(plan.PlanError, "unsafe changed path"):
            plan.build_plan(data)

    def test_nonready_dependents_do_not_require_file_evidence(self):
        result = plan.build_plan(payload([dep(1, ready=False)], {}))
        self.assertEqual("empty", result["status"])
        self.assertEqual(0, result["candidate_count"])

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "input.json"
            real.write_text(json.dumps(payload([], {})), encoding="utf-8")
            link = root / "input-link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(plan.PlanError, "symlink"):
                plan._load(link)

    def test_workflow_is_read_only_exact_head_and_never_merges(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("needs: validate", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
            text,
        )
        self.assertIn("node-version: 24", text)
        self.assertIn("python3 -m unittest discover -v", text)
        self.assertIn("tests/test_birds_eye_review.sh", text)
        self.assertIn("tests.test_transactional_promote", text)
        self.assertIn("scripts/zcloud_config_validate.py", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("merge_authorized", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("git push", text)


if __name__ == "__main__":
    unittest.main()
