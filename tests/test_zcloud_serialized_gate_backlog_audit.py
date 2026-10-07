#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_gate_backlog_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-gate-backlog-audit.yml"
SPEC = importlib.util.spec_from_file_location("serialized_gate_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

MAIN = "1" * 40
OLD = "2" * 40
HEAD = "3" * 40


def gate(number: int, *, state: str = "open", merged: bool = False, draft: bool = False):
    return {
        "number": number,
        "state": state,
        "merged": merged,
        "draft": draft,
        "head_sha": HEAD,
        "base_sha": OLD,
    }


def pr(
    number: int,
    *,
    body: str,
    draft: bool = False,
    base_sha: str = MAIN,
):
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
        "gates": gates if gates is not None else [gate(580), gate(576)],
        "pull_requests": pulls or [],
    }


class SerializedGateBacklogAuditTests(unittest.TestCase):
    def test_open_gates_block_and_classify_dependent_backlog(self):
        payload = snapshot(
            [
                pr(1001, body="Keep unmerged behind #580/PWQ-41 + #576 serialized live window."),
                pr(
                    1002,
                    body="Integration remains serialized behind #580 and #576.",
                    draft=True,
                ),
                pr(
                    1003,
                    body="Keep unmerged behind #580 + #576 serialized window.",
                    base_sha=OLD,
                ),
                pr(1004, body="mentions #580 only; unrelated"),
            ]
        )
        result = audit.audit_snapshot(payload)
        self.assertEqual("blocked", result["status"])
        self.assertTrue(result["gate_open"])
        self.assertEqual(3, result["dependent_pr_count"])
        self.assertEqual(1, result["review_ready_current_main_count"])
        self.assertEqual(1, result["stale_base_count"])
        self.assertEqual(1, result["draft_count"])
        self.assertFalse(result["mutation_performed"])

    def test_closed_gates_release_existing_backlog(self):
        gates = [
            gate(580, state="closed", merged=False),
            gate(576, state="closed", merged=True),
        ]
        result = audit.audit_snapshot(
            snapshot(
                [pr(1007, body="Keep unmerged behind #580/#576 serialized integration window.")],
                gates=gates,
            )
        )
        self.assertFalse(result["gate_open"])
        self.assertTrue(result["integration_released"])
        self.assertEqual("released_with_backlog", result["status"])

    def test_gate_prs_are_not_counted_as_dependents(self):
        pulls = [
            pr(580, body="#580 #576 serialized"),
            pr(576, body="#580 #576 serialized"),
        ]
        result = audit.audit_snapshot(snapshot(pulls))
        self.assertEqual(0, result["dependent_pr_count"])

    def test_missing_gate_snapshot_fails_closed(self):
        with self.assertRaisesRegex(audit.SnapshotError, "missing gate"):
            audit.audit_snapshot(snapshot(gates=[gate(580)]))

    def test_incomplete_inventory_fails_closed(self):
        payload = snapshot()
        payload["inventory_complete"] = False
        with self.assertRaisesRegex(audit.SnapshotError, "inventory incomplete"):
            audit.audit_snapshot(payload)

    def test_missing_inventory_completeness_fails_closed(self):
        payload = snapshot()
        del payload["inventory_complete"]
        with self.assertRaisesRegex(audit.SnapshotError, "inventory_complete"):
            audit.audit_snapshot(payload)

    def test_duplicate_open_pr_identity_fails_closed(self):
        body = "#580 and #576 serialized live writer window"
        with self.assertRaisesRegex(audit.SnapshotError, "duplicate pull request"):
            audit.audit_snapshot(snapshot([pr(900, body=body), pr(900, body=body)]))

    def test_non_open_inventory_entry_fails_closed(self):
        item = pr(901, body="#580 and #576 serialized")
        item["state"] = "closed"
        with self.assertRaisesRegex(audit.SnapshotError, "non-open PR"):
            audit.audit_snapshot(snapshot([item]))

    def test_output_never_emits_titles_or_bodies(self):
        secret_phrase = "do-not-echo-this-body"
        result = audit.audit_snapshot(
            snapshot(
                [
                    pr(
                        902,
                        body=f"{secret_phrase} #580 #576 serialized integration window",
                    )
                ]
            )
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn(secret_phrase, rendered)
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"title"', rendered)

    def test_snapshot_loader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "snapshot.json"
            real.write_text(json.dumps(snapshot()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(audit.SnapshotError, "symlink"):
                audit._load_json(link)

    def test_cli_rejects_symlink_without_dereferencing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "snapshot.json"
            real.write_text(json.dumps(snapshot()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            code = audit.main(["--snapshot", str(link), "--json"])
        self.assertEqual(1, code)

    def test_cli_require_released_returns_two_while_gate_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            code = audit.main(["--snapshot", str(path), "--require-released", "--json"])
        self.assertEqual(2, code)

    def test_workflow_is_exact_head_read_only_and_owner_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("mutation_performed", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("systemctl restart", text)


if __name__ == "__main__":
    unittest.main()
