#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_metadata_drift_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-metadata-drift-audit.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_metadata_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

MAIN = "1" * 40
OLD = "2" * 40
HEAD = "3" * 40


def pr(number: int, *, body: str, draft: bool = False, base_sha: str = MAIN):
    return {
        "number": number,
        "state": "open",
        "draft": draft,
        "head_sha": HEAD,
        "base_sha": base_sha,
        "body": body,
    }


def snapshot(pulls=None):
    return {
        "repository": "Zennay/zCloud",
        "current_main_sha": MAIN,
        "inventory_complete": True,
        "gate_succession": {
            "primary_gate": 580,
            "retired_gate": 576,
            "successor_gate": 1089,
            "retired_state": "closed",
            "retired_merged": False,
            "retired_head_sha": OLD,
            "successor_state": "open",
            "successor_merged": False,
            "successor_head_sha": HEAD,
            "successor_base_sha": MAIN,
        },
        "pull_requests": pulls or [],
    }


class SerializedGateMetadataDriftAuditTests(unittest.TestCase):
    def test_flags_retired_gate_reference_without_successor(self):
        result = audit.audit_snapshot(
            snapshot([
                pr(900, body="Keep unmerged behind #580/PWQ-41 + #576 serialized window.")
            ])
        )
        self.assertEqual("drift_detected", result["status"])
        self.assertEqual([900], result["stale_reference_prs"])
        self.assertEqual(1, result["stale_reference_count"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_successor_pr_historical_provenance_is_not_stale(self):
        result = audit.audit_snapshot(
            snapshot([
                pr(
                    1089,
                    body="Supersedes #576; keep unmerged while #580/PWQ-41 owns the serialized window.",
                )
            ])
        )
        self.assertEqual("clean", result["status"])
        self.assertEqual([], result["stale_reference_prs"])
        self.assertEqual(0, result["serialized_primary_pr_count"])

    def test_successor_aware_reference_is_not_stale(self):
        result = audit.audit_snapshot(
            snapshot([
                pr(
                    901,
                    body="Keep unmerged behind #580/PWQ-41 + #1089; #576 is the retired serialized predecessor.",
                )
            ])
        )
        self.assertEqual("clean", result["status"])
        self.assertEqual([], result["stale_reference_prs"])
        self.assertEqual(1, result["successor_aware_pr_count"])

    def test_primary_only_current_coordination_is_not_stale(self):
        result = audit.audit_snapshot(
            snapshot([
                pr(902, body="Keep unmerged while #580/PWQ-41 owns the serialized control-plane window.")
            ])
        )
        self.assertEqual("clean", result["status"])
        self.assertEqual(1, result["serialized_primary_pr_count"])

    def test_unrelated_historical_reference_is_ignored(self):
        result = audit.audit_snapshot(
            snapshot([pr(903, body="Historical note: #576 existed before replacement.")])
        )
        self.assertEqual("clean", result["status"])
        self.assertEqual(0, result["serialized_primary_pr_count"])

    def test_retired_gate_must_be_closed_unmerged(self):
        payload = snapshot()
        payload["gate_succession"]["retired_state"] = "open"
        with self.assertRaisesRegex(audit.AuditError, "not closed"):
            audit.audit_snapshot(payload)
        payload = snapshot()
        payload["gate_succession"]["retired_merged"] = True
        with self.assertRaisesRegex(audit.AuditError, "unexpectedly merged"):
            audit.audit_snapshot(payload)

    def test_duplicate_or_non_open_inventory_fails_closed(self):
        item = pr(904, body="#580 #576 serialized")
        with self.assertRaisesRegex(audit.AuditError, "duplicate pull request"):
            audit.audit_snapshot(snapshot([item, item.copy()]))
        closed = pr(905, body="#580 #576 serialized")
        closed["state"] = "closed"
        with self.assertRaisesRegex(audit.AuditError, "non-open PR"):
            audit.audit_snapshot(snapshot([closed]))

    def test_output_is_body_and_title_free(self):
        secret = "do-not-echo-this-coordination-body"
        result = audit.audit_snapshot(
            snapshot([
                pr(906, body=f"{secret} #580 + #576 serialized live writer window")
            ])
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"title"', rendered)

    def test_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "snapshot.json"
            real.write_text(json.dumps(snapshot()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(audit.AuditError, "symlink"):
                audit._load_json(link)

    def test_cli_require_clean_returns_two_on_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(
                json.dumps(snapshot([
                    pr(907, body="#580 + #576 serialized integration window")
                ])),
                encoding="utf-8",
            )
            code = audit.main(["--snapshot", str(path), "--require-clean", "--json"])
        self.assertEqual(2, code)

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
        self.assertIn("repos/{repo}/pulls/576", text)
        self.assertIn("repos/{repo}/pulls/1089", text)
        self.assertIn("mutation_performed", text)
        self.assertIn("release_authorized", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("gh pr edit", text)
        self.assertNotIn("gh pr merge", text)


if __name__ == "__main__":
    unittest.main()
