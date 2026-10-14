#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_metadata_rewrite_preview.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-metadata-rewrite-preview.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_metadata_rewrite_preview", SCRIPT)
assert SPEC and SPEC.loader
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)

MAIN = "1" * 40
HEAD = "2" * 40
ACTIVE = "Keep unmerged behind #580/PWQ-41 + #576 serialized control-plane window."
HISTORY = "Historical provenance: #576 replaced an older deploy summary."
BODY = ACTIVE + "\n\n" + HISTORY + "\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def intent(body=BODY):
    return {
        "schema": "zcloud-serialized-gate-metadata-remediation-intent-v1",
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "status": "intent_ready",
        "intent_count": 1,
        "intents": [{
            "number": 10,
            "expected_head_sha": HEAD,
            "expected_body_sha256": sha(body),
            "old_marker": "#576",
            "new_marker": "#1089",
        }],
        "compare_and_swap_required": True,
        "requires_fresh_body_hash_match": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def snapshot(body=BODY, head_sha=HEAD, state="open"):
    return {
        "schema": "zcloud-serialized-gate-metadata-rewrite-preview-snapshot-v1",
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


class SerializedGateMetadataRewritePreviewTests(unittest.TestCase):
    def test_preview_is_line_scoped_and_preserves_historical_reference(self):
        result = preview.build_preview(intent(), snapshot())
        self.assertEqual("preview_ready", result["status"])
        self.assertEqual(1, result["preview_count"])
        item = result["previews"][0]
        expected = ACTIVE.replace("#576", "#1089") + "\n\n" + HISTORY + "\n"
        self.assertEqual(sha(BODY), item["original_body_sha256"])
        self.assertEqual(sha(expected), item["proposed_body_sha256"])
        self.assertEqual(1, item["changed_line_count"])
        self.assertEqual(1, item["replacement_count"])
        self.assertEqual(1, item["preserved_retired_reference_count"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn(ACTIVE, rendered)
        self.assertNotIn(HISTORY, rendered)
        self.assertNotIn('"body"', rendered)
        self.assertTrue(result["line_scoped_rewrite_required"])
        self.assertTrue(result["compare_and_swap_required"])
        self.assertFalse(result["metadata_write_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_multiple_active_lines_are_bounded_and_rewritten(self):
        body = ACTIVE + "\n" + ACTIVE.replace("control-plane", "integration") + "\n" + HISTORY
        result = preview.build_preview(intent(body), snapshot(body))
        item = result["previews"][0]
        self.assertEqual(2, item["changed_line_count"])
        self.assertEqual(2, item["replacement_count"])
        self.assertEqual(1, item["preserved_retired_reference_count"])

    def test_body_or_head_drift_fails_closed(self):
        with self.assertRaisesRegex(preview.PreviewError, "body hash changed"):
            preview.build_preview(intent(), snapshot(body=BODY + "changed"))
        with self.assertRaisesRegex(preview.PreviewError, "head SHA changed"):
            preview.build_preview(intent(), snapshot(head_sha="3" * 40))

    def test_successor_aware_or_historical_only_body_has_no_target(self):
        successor_body = ACTIVE.replace("#576", "#1089") + "\n" + HISTORY
        with self.assertRaisesRegex(preview.PreviewError, "no line-scoped rewrite target"):
            preview.build_preview(intent(successor_body), snapshot(successor_body))
        historical = HISTORY + "\n"
        with self.assertRaisesRegex(preview.PreviewError, "no line-scoped rewrite target"):
            preview.build_preview(intent(historical), snapshot(historical))

    def test_changed_line_bound_fails_closed(self):
        body = "\n".join([ACTIVE] * 6)
        with self.assertRaisesRegex(preview.PreviewError, "changed-line bound"):
            preview.build_preview(intent(body), snapshot(body))

    def test_marker_mismatch_or_authorized_intent_fails_closed(self):
        value = intent()
        value["intents"][0]["new_marker"] = "#999"
        with self.assertRaisesRegex(preview.PreviewError, "marker mismatch"):
            preview.build_preview(value, snapshot())
        value = intent()
        value["metadata_write_authorized"] = True
        with self.assertRaisesRegex(preview.PreviewError, "unexpectedly sets"):
            preview.build_preview(value, snapshot())

    def test_closed_successor_or_target_fails_closed(self):
        snap = snapshot()
        snap["successor_open"] = False
        with self.assertRaisesRegex(preview.PreviewError, "successor gate"):
            preview.build_preview(intent(), snap)
        with self.assertRaisesRegex(preview.PreviewError, "no longer open"):
            preview.build_preview(intent(), snapshot(state="closed"))

    def test_clean_intent_produces_clean_preview(self):
        clean = {
            "schema": "zcloud-serialized-gate-metadata-remediation-intent-v1",
            "repository": "Zennay/zCloud",
            "current_main_sha": MAIN,
            "status": "clean",
            "intent_count": 0,
            "intents": [],
            "compare_and_swap_required": True,
            "requires_fresh_body_hash_match": True,
            "metadata_write_authorized": False,
            "merge_authorized": False,
            "deploy_authorized": False,
            "mutation_performed": False,
        }
        snap = snapshot()
        snap["pull_requests"] = []
        result = preview.build_preview(clean, snap)
        self.assertEqual("clean", result["status"])
        self.assertEqual([], result["previews"])

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "intent.json"
            real.write_text(json.dumps(intent()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(preview.PreviewError, "symlink"):
                preview._load_json(link, "intent")

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
        self.assertIn("proposed_body_sha256", text)
        self.assertIn("line_scoped_rewrite_required", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("gh pr edit", text)
        self.assertNotIn("git push", text)


if __name__ == "__main__":
    unittest.main()
