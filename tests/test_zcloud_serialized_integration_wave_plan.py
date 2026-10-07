#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_integration_wave_plan.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-integration-wave-plan.yml"
SPEC = importlib.util.spec_from_file_location("serialized_integration_plan", SCRIPT)
assert SPEC and SPEC.loader
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

MAIN = "1" * 40
OLD = "2" * 40
HEAD = "3" * 40
BODY = "Keep unmerged behind #580/PWQ-41 + #576 serialized live writer window."


def gate(number: int, *, state: str = "open", merged: bool = False, draft: bool = False):
    return {
        "number": number,
        "state": state,
        "merged": merged,
        "draft": draft,
        "head_sha": HEAD,
        "base_sha": OLD,
    }


def pr(number: int, *, body: str = BODY, draft: bool = False, base_sha: str = MAIN):
    return {
        "number": number,
        "state": "open",
        "draft": draft,
        "head_sha": HEAD,
        "base_sha": base_sha,
        "body": body,
    }


def snapshot(pulls=None, gates=None):
    return {
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "inventory_complete": True,
        "gates": gates if gates is not None else [gate(580), gate(1089)],
        "pull_requests": pulls or [],
    }


def files(mapping):
    return {
        str(number): {"complete": True, "files": paths}
        for number, paths in mapping.items()
    }


class SerializedIntegrationWavePlanTests(unittest.TestCase):
    def test_partitions_candidates_into_file_disjoint_waves(self):
        result = plan.build_plan(
            snapshot([pr(40), pr(20), pr(10), pr(30)]),
            files(
                {
                    10: ["a.py"],
                    20: ["b.py", "c.py"],
                    30: ["a.py", "d.py"],
                    40: ["e.py"],
                }
            ),
            wave_size=10,
        )
        self.assertEqual("blocked_preview", result["status"])
        self.assertEqual(2, result["wave_count"])
        self.assertEqual(
            [10, 40, 20],
            [item["number"] for item in result["waves"][0]["pull_requests"]],
        )
        self.assertEqual(
            [30],
            [item["number"] for item in result["waves"][1]["pull_requests"]],
        )
        self.assertTrue(all(wave["file_disjoint"] for wave in result["waves"]))
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["auto_merge_allowed"])

    def test_wave_size_caps_each_wave_deterministically(self):
        result = plan.build_plan(
            snapshot([pr(3), pr(1), pr(2)]),
            files({1: ["a"], 2: ["b"], 3: ["c"]}),
            wave_size=2,
        )
        self.assertEqual([1, 2], [x["number"] for x in result["waves"][0]["pull_requests"]])
        self.assertEqual([3], [x["number"] for x in result["waves"][1]["pull_requests"]])

    def test_successor_reference_is_a_candidate(self):
        successor_body = "Keep unmerged behind #580/PWQ-41 + #1089 serialized live writer window."
        classified = plan.classify_snapshot(snapshot([pr(1090, body=successor_body)]))
        self.assertEqual([1090], classified["ready_pull_requests"])

    def test_legacy_predecessor_reference_remains_a_candidate(self):
        classified = plan.classify_snapshot(snapshot([pr(1001)]))
        self.assertEqual([1001], classified["ready_pull_requests"])

    def test_open_successor_blocks_after_predecessor_closes(self):
        gates = [
            gate(580, state="closed", merged=False),
            gate(1089, state="open", merged=False),
        ]
        classified = plan.classify_snapshot(
            snapshot(
                [pr(1090, body="Keep unmerged behind #580 + #1089 serialized window.")],
                gates=gates,
            )
        )
        self.assertTrue(classified["gate_open"])
        self.assertFalse(classified["integration_released"])

    def test_released_gate_marks_plan_usable_but_never_authorizes_merge(self):
        gates = [gate(580, state="closed", merged=True), gate(1089, state="closed", merged=True)]
        result = plan.build_plan(snapshot([pr(1)], gates=gates), files({1: ["a"]}))
        self.assertEqual("released_candidate_waves", result["status"])
        self.assertTrue(result["integration_released"])
        self.assertTrue(result["executable"])
        self.assertFalse(result["preview_only"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["auto_merge_allowed"])
        self.assertTrue(result["ci_revalidation_required"])
        self.assertTrue(result["ownership_revalidation_required"])
        self.assertTrue(result["requires_per_pr_revalidation"])
        self.assertIn("then-current main", result["revalidation_rule"])

    def test_draft_and_stale_are_excluded_from_changed_file_requirement(self):
        result = plan.build_plan(
            snapshot([pr(1), pr(2, draft=True), pr(3, base_sha=OLD)]),
            files({1: ["a"]}),
        )
        self.assertEqual(3, result["dependent_pr_count"])
        self.assertEqual(1, result["ready_current_main_count"])
        self.assertEqual([3], result["stale_base_pull_requests"])
        self.assertEqual([2], result["draft_pull_requests"])

    def test_extra_changed_file_evidence_fails_closed(self):
        with self.assertRaisesRegex(plan.PlanError, "key set mismatch"):
            plan.build_plan(snapshot([pr(1)]), files({1: ["a"], 2: ["b"]}))

    def test_missing_changed_file_evidence_fails_closed(self):
        with self.assertRaisesRegex(plan.PlanError, "key set mismatch"):
            plan.build_plan(snapshot([pr(1)]), {})

    def test_incomplete_changed_file_evidence_fails_closed(self):
        evidence = files({1: ["a"]})
        evidence["1"]["complete"] = False
        with self.assertRaisesRegex(plan.PlanError, "incomplete changed-file"):
            plan.build_plan(snapshot([pr(1)]), evidence)

    def test_unsafe_or_duplicate_paths_fail_closed(self):
        with self.assertRaisesRegex(plan.PlanError, "unsafe changed path"):
            plan.build_plan(snapshot([pr(1)]), files({1: ["../server.py"]}))
        with self.assertRaisesRegex(plan.PlanError, "duplicate changed path"):
            plan.build_plan(snapshot([pr(1)]), files({1: ["a.py", "a.py"]}))

    def test_incomplete_inventory_and_incoherent_gate_fail_closed(self):
        bad = snapshot()
        bad["inventory_complete"] = False
        with self.assertRaisesRegex(plan.PlanError, "inventory incomplete"):
            plan.classify_snapshot(bad)
        with self.assertRaisesRegex(plan.PlanError, "incoherent gate"):
            plan.classify_snapshot(
                snapshot(gates=[gate(580, state="open", merged=True), gate(1089)])
            )

    def test_ready_inventory_is_body_free(self):
        secret = "sensitive-body-marker"
        classified = plan.classify_snapshot(
            snapshot([pr(1, body=f"{secret} #580 #576 serialized integration window")])
        )
        rendered = json.dumps(classified, sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertEqual([1], classified["ready_pull_requests"])

    def test_gate_prs_and_unrelated_prs_are_not_candidates(self):
        classified = plan.classify_snapshot(
            snapshot(
                [
                    pr(580),
                    pr(1089),
                    pr(100, body="#580 only; unrelated"),
                    pr(101),
                ]
            )
        )
        self.assertEqual([101], classified["ready_pull_requests"])

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "snapshot.json"
            real.write_text(json.dumps(snapshot()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(plan.PlanError, "symlink"):
                plan._load_json(link, "snapshot")

    def test_cli_list_ready_needs_no_changed_file_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(snapshot([pr(1)])), encoding="utf-8")
            code = plan.main(["--snapshot", str(path), "--list-ready", "--json"])
        self.assertEqual(0, code)

    def test_cli_require_released_returns_two_while_gate_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snapshot.json"
            evidence = Path(tmp) / "files.json"
            snap.write_text(json.dumps(snapshot([pr(1)])), encoding="utf-8")
            evidence.write_text(json.dumps(files({1: ["a"]})), encoding="utf-8")
            code = plan.main(
                [
                    "--snapshot", str(snap),
                    "--files", str(evidence),
                    "--require-released",
                    "--json",
                ]
            )
        self.assertEqual(2, code)

    def test_workflow_is_exact_head_read_only_owner_guarded_and_never_merges(self):
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
        self.assertIn("files?per_page=100&page=", text)
        self.assertIn("--list-ready", text)
        self.assertIn("main moved during changed-file evidence capture", text)
        self.assertIn("serialized gate #{number} moved during evidence capture", text)
        self.assertIn("for number in (580, 1089):", text)
        self.assertIn("merge_authorized", text)
        self.assertIn("requires_per_pr_revalidation", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("workflow dispatch", text)


if __name__ == "__main__":
    unittest.main()
