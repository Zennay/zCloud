#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_metadata_batch_preflight.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-metadata-batch-preflight.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_metadata_batch_preflight", SCRIPT)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

MAIN = "1" * 40
HEAD1 = "2" * 40
HEAD2 = "3" * 40


def plan(numbers=None):
    if numbers is None:
        numbers = [10, 11]
    numbers = list(numbers)
    return {
        "schema": "zcloud-serialized-gate-metadata-remediation-plan-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "status": "remediation_needed" if numbers else "clean",
        "primary_gate": 580,
        "retired_gate": 576,
        "successor_gate": 1089,
        "stale_reference_count": len(numbers),
        "batch_size": 20,
        "batch_count": 1 if numbers else 0,
        "batches": (
            [{"batch_index": 1, "count": len(numbers), "pr_numbers": numbers}]
            if numbers else []
        ),
        "requires_fresh_revalidation": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def pull(number, *, body, state="open", draft=False, head_sha=HEAD1):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "head_sha": head_sha,
        "body": body,
    }


def snapshot(pulls):
    return {
        "schema": "zcloud-serialized-gate-metadata-batch-preflight-snapshot-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "successor_gate": 1089,
        "successor_open": True,
        "pull_requests": pulls,
    }


STALE = "Keep unmerged behind #580/PWQ-41 + #576 serialized control-plane window."


class SerializedGateMetadataBatchPreflightTests(unittest.TestCase):
    def test_ready_batch_emits_only_number_sha_and_draft(self):
        result = preflight.preflight_batch(
            plan([10, 11]),
            snapshot([
                pull(10, body=STALE, head_sha=HEAD1),
                pull(11, body=STALE, draft=True, head_sha=HEAD2),
            ]),
            batch_index=1,
        )
        self.assertEqual("ready_for_review", result["status"])
        self.assertEqual(2, result["eligible_count"])
        self.assertEqual(
            [
                {"number": 10, "head_sha": HEAD1, "draft": False},
                {"number": 11, "head_sha": HEAD2, "draft": True},
            ],
            result["eligible"],
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("Keep unmerged", rendered)
        self.assertNotIn('"body"', rendered)
        self.assertFalse(result["metadata_write_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_changed_targets_require_refresh(self):
        result = preflight.preflight_batch(
            plan([10, 11, 12, 13, 14]),
            snapshot([
                pull(10, body=STALE),
                pull(11, body=STALE, state="closed"),
                pull(12, body="Keep unmerged behind #580/PWQ-41 + #1089; #576 retired serialized."),
                pull(13, body="Keep unmerged behind #580/PWQ-41 serialized window."),
                pull(14, body="#576 historical note only."),
            ]),
            batch_index=1,
        )
        self.assertEqual("refresh_required", result["status"])
        self.assertEqual(1, result["eligible_count"])
        self.assertEqual(
            [
                {"number": 11, "reason": "closed"},
                {"number": 12, "reason": "successor_aware"},
                {"number": 13, "reason": "retired_reference_removed"},
                {"number": 14, "reason": "serialized_context_removed"},
            ],
            result["ineligible"],
        )

    def test_missing_target_requires_refresh(self):
        result = preflight.preflight_batch(
            plan([10, 11]),
            snapshot([pull(10, body=STALE)]),
            batch_index=1,
        )
        self.assertEqual([{"number": 11, "reason": "missing"}], result["ineligible"])

    def test_stale_plan_main_fails_closed(self):
        snap = snapshot([pull(10, body=STALE), pull(11, body=STALE)])
        snap["current_main_sha"] = "4" * 40
        with self.assertRaisesRegex(preflight.PreflightError, "main SHA is stale"):
            preflight.preflight_batch(plan([10, 11]), snap, batch_index=1)

    def test_successor_must_remain_open(self):
        snap = snapshot([pull(10, body=STALE), pull(11, body=STALE)])
        snap["successor_open"] = False
        with self.assertRaisesRegex(preflight.PreflightError, "no longer open"):
            preflight.preflight_batch(plan([10, 11]), snap, batch_index=1)

    def test_plan_with_write_authorization_is_rejected(self):
        p = plan([10])
        p["metadata_write_authorized"] = True
        with self.assertRaisesRegex(preflight.PreflightError, "unexpectedly sets"):
            preflight.preflight_batch(p, snapshot([pull(10, body=STALE)]), batch_index=1)

    def test_duplicate_or_oversized_batch_fails_closed(self):
        p = plan([10, 10])
        with self.assertRaisesRegex(preflight.PreflightError, "duplicate planned"):
            preflight.preflight_batch(p, snapshot([pull(10, body=STALE)]), batch_index=1)
        numbers = list(range(1, 52))
        p = plan(numbers)
        with self.assertRaisesRegex(preflight.PreflightError, "bounded limit"):
            preflight.preflight_batch(
                p,
                snapshot([pull(n, body=STALE) for n in numbers]),
                batch_index=1,
            )

    def test_clean_plan_returns_clean_without_targets(self):
        result = preflight.preflight_batch(
            plan([]),
            snapshot([]),
            batch_index=1,
        )
        self.assertEqual("clean", result["status"])
        self.assertEqual([], result["eligible"])
        self.assertFalse(result["metadata_write_authorized"])

    def test_loaders_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "plan.json"
            real.write_text(json.dumps(plan([10])), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(preflight.PreflightError, "symlink"):
                preflight._load_json(link, "plan")

    def test_cli_require_ready_returns_two_on_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            snap_path = root / "snapshot.json"
            plan_path.write_text(json.dumps(plan([10])), encoding="utf-8")
            snap_path.write_text(json.dumps(snapshot([])), encoding="utf-8")
            code = preflight.main([
                "--plan", str(plan_path),
                "--snapshot", str(snap_path),
                "--require-ready",
                "--json",
            ])
        self.assertEqual(2, code)

    def test_workflow_is_read_only_exact_head_and_vps_guarded(self):
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
        self.assertIn("metadata_write_authorized", text)
        self.assertIn("requires_fresh_revalidation", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh pr edit", text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("git push", text)


if __name__ == "__main__":
    unittest.main()
