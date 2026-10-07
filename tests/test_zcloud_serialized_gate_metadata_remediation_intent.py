#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_metadata_remediation_intent.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-metadata-remediation-intent.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_metadata_intent", SCRIPT)
assert SPEC and SPEC.loader
intent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(intent)

MAIN = "1" * 40
HEAD = "2" * 40
BODY = "Keep unmerged behind #580/PWQ-41 + #576 serialized control-plane window."


def preflight(eligible=None):
    eligible = list(eligible or [{"number": 10, "head_sha": HEAD, "draft": False}])
    return {
        "schema": "zcloud-serialized-gate-metadata-batch-preflight-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "status": "ready_for_review" if eligible else "clean",
        "batch_index": 1 if eligible else None,
        "planned_count": len(eligible),
        "eligible_count": len(eligible),
        "ineligible_count": 0,
        "eligible": eligible,
        "ineligible": [],
        "requires_fresh_revalidation": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def snapshot(body=BODY, head_sha=HEAD, state="open"):
    return {
        "schema": "zcloud-serialized-gate-metadata-remediation-intent-snapshot-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "primary_gate": 580,
        "retired_gate": 576,
        "successor_gate": 1089,
        "successor_open": True,
        "pull_requests": [{
            "number": 10,
            "state": state,
            "head_sha": head_sha,
            "body": body,
        }],
    }


class SerializedGateMetadataRemediationIntentTests(unittest.TestCase):
    def test_builds_body_hash_bound_intent_without_echoing_body(self):
        result = intent.build_intent(preflight(), snapshot())
        self.assertEqual("intent_ready", result["status"])
        self.assertEqual(1, result["intent_count"])
        self.assertEqual(10, result["intents"][0]["number"])
        self.assertEqual(HEAD, result["intents"][0]["expected_head_sha"])
        self.assertEqual(
            hashlib.sha256(BODY.encode("utf-8")).hexdigest(),
            result["intents"][0]["expected_body_sha256"],
        )
        self.assertEqual("#576", result["intents"][0]["old_marker"])
        self.assertEqual("#1089", result["intents"][0]["new_marker"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn(BODY, rendered)
        self.assertNotIn('"body"', rendered)
        self.assertTrue(result["compare_and_swap_required"])
        self.assertTrue(result["requires_fresh_body_hash_match"])
        self.assertFalse(result["metadata_write_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_changed_head_fails_closed(self):
        with self.assertRaisesRegex(intent.IntentError, "head SHA changed"):
            intent.build_intent(preflight(), snapshot(head_sha="3" * 40))

    def test_changed_body_state_fails_closed(self):
        with self.assertRaisesRegex(intent.IntentError, "already successor-aware"):
            intent.build_intent(
                preflight(),
                snapshot(body="Keep unmerged behind #580 + #1089; #576 retired serialized."),
            )
        with self.assertRaisesRegex(intent.IntentError, "no longer references retired"):
            intent.build_intent(
                preflight(),
                snapshot(body="Keep unmerged behind #580 serialized control-plane window."),
            )
        with self.assertRaisesRegex(intent.IntentError, "lost serialized context"):
            intent.build_intent(preflight(), snapshot(body="#576 historical note"))

    def test_closed_or_missing_target_fails_closed(self):
        with self.assertRaisesRegex(intent.IntentError, "no longer open"):
            intent.build_intent(preflight(), snapshot(state="closed"))
        snap = snapshot()
        snap["pull_requests"] = []
        with self.assertRaisesRegex(intent.IntentError, "missing"):
            intent.build_intent(preflight(), snap)

    def test_stale_main_or_closed_successor_fails_closed(self):
        snap = snapshot()
        snap["current_main_sha"] = "4" * 40
        with self.assertRaisesRegex(intent.IntentError, "main SHA is stale"):
            intent.build_intent(preflight(), snap)
        snap = snapshot()
        snap["successor_open"] = False
        with self.assertRaisesRegex(intent.IntentError, "no longer open"):
            intent.build_intent(preflight(), snap)

    def test_write_authorizing_or_ineligible_preflight_is_rejected(self):
        p = preflight()
        p["metadata_write_authorized"] = True
        with self.assertRaisesRegex(intent.IntentError, "unexpectedly sets"):
            intent.build_intent(p, snapshot())
        p = preflight()
        p["ineligible_count"] = 1
        with self.assertRaisesRegex(intent.IntentError, "ineligible targets"):
            intent.build_intent(p, snapshot())

    def test_clean_preflight_produces_clean_manifest(self):
        result = intent.build_intent(
            preflight([]),
            {
                "schema": "zcloud-serialized-gate-metadata-remediation-intent-snapshot-v1",
                "repository": "Zennay/zCloud",
                "current_main_sha": MAIN,
                "primary_gate": 580,
                "retired_gate": 576,
                "successor_gate": 1089,
                "successor_open": True,
                "pull_requests": [],
            },
        )
        self.assertEqual("clean", result["status"])
        self.assertEqual([], result["intents"])

    def test_duplicate_targets_fail_closed(self):
        p = preflight([
            {"number": 10, "head_sha": HEAD, "draft": False},
            {"number": 10, "head_sha": HEAD, "draft": False},
        ])
        with self.assertRaisesRegex(intent.IntentError, "duplicate eligible"):
            intent.build_intent(p, snapshot())

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "preflight.json"
            real.write_text(json.dumps(preflight()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(intent.IntentError, "symlink"):
                intent._load_json(link, "preflight")

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
        self.assertIn("expected_body_sha256", text)
        self.assertIn("metadata_write_authorized", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh pr edit", text)
        self.assertNotIn("git push", text)


if __name__ == "__main__":
    unittest.main()
