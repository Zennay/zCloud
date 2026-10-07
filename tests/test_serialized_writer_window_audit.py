import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.zcloud_serialized_writer_window_audit import (
    WindowEvidenceError,
    audit_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_serialized_writer_window_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-serialized-writer-window-audit.yml"


class SerializedWriterWindowAuditTests(unittest.TestCase):
    def now(self):
        return datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)

    def owner(
        self,
        ref,
        *,
        kind="pull_request",
        state="open",
        release_proven=False,
    ):
        return {
            "kind": kind,
            "ref": ref,
            "state": state,
            "release_proven": release_proven,
        }

    def snapshot(self, owners):
        return {
            "schema_version": 1,
            "observed_at": "2026-10-07T06:55:00Z",
            "window_id": "control-plane-live-writer",
            "inventory_complete": True,
            "owners": owners,
        }

    def test_current_serialized_owners_block_window(self):
        result = audit_snapshot(
            self.snapshot(
                [
                    self.owner("pr:580"),
                    self.owner("pr:576"),
                ]
            ),
            now=self.now(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["pr:576", "pr:580"], result["active_owner_refs"])
        self.assertEqual([], result["released_owner_refs"])
        self.assertFalse(result["mutation_performed"])

    def test_all_terminal_proven_owners_clear_window(self):
        result = audit_snapshot(
            self.snapshot(
                [
                    self.owner(
                        "pr:580",
                        state="merged",
                        release_proven=True,
                    ),
                    self.owner(
                        "pr:576",
                        state="closed",
                        release_proven=True,
                    ),
                ]
            ),
            now=self.now(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("clear", result["status"])
        self.assertEqual([], result["active_owner_refs"])
        self.assertEqual(["pr:576", "pr:580"], result["released_owner_refs"])

    def test_terminal_owner_without_release_proof_is_incomplete(self):
        result = audit_snapshot(
            self.snapshot(
                [
                    self.owner(
                        "pr:580",
                        state="closed",
                        release_proven=False,
                    )
                ]
            ),
            now=self.now(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual("incomplete", result["status"])
        self.assertEqual(["pr:580"], result["unproven_release_refs"])

    def test_worker_owner_uses_same_fail_closed_semantics(self):
        blocked = audit_snapshot(
            self.snapshot(
                [
                    self.owner(
                        "worker:cloud-writer",
                        kind="worker",
                        state="active",
                    )
                ]
            ),
            now=self.now(),
        )
        self.assertEqual("blocked", blocked["status"])

        clear = audit_snapshot(
            self.snapshot(
                [
                    self.owner(
                        "worker:cloud-writer",
                        kind="worker",
                        state="released",
                        release_proven=True,
                    )
                ]
            ),
            now=self.now(),
        )
        self.assertEqual("clear", clear["status"])

    def test_active_owner_cannot_claim_release_proof(self):
        with self.assertRaisesRegex(
            WindowEvidenceError,
            "active_owner_marked_released",
        ):
            audit_snapshot(
                self.snapshot(
                    [
                        self.owner(
                            "pr:580",
                            release_proven=True,
                        )
                    ]
                ),
                now=self.now(),
            )

    def test_duplicate_owner_is_rejected(self):
        with self.assertRaisesRegex(WindowEvidenceError, "owner_duplicate"):
            audit_snapshot(
                self.snapshot(
                    [
                        self.owner("pr:580"),
                        self.owner("pr:580"),
                    ]
                ),
                now=self.now(),
            )

    def test_kind_and_ref_must_agree(self):
        with self.assertRaisesRegex(WindowEvidenceError, "owner_ref_invalid"):
            audit_snapshot(
                self.snapshot(
                    [
                        self.owner(
                            "issue:580",
                            kind="pull_request",
                        )
                    ]
                ),
                now=self.now(),
            )

    def test_rejects_unknown_owner_fields(self):
        snap = self.snapshot([self.owner("pr:580")])
        snap["owners"][0]["title"] = "must never enter bounded evidence"
        with self.assertRaisesRegex(WindowEvidenceError, "owner_invalid"):
            audit_snapshot(snap, now=self.now())

    def test_incomplete_inventory_fails_closed(self):
        snap = self.snapshot([self.owner("pr:580")])
        snap["inventory_complete"] = False
        with self.assertRaisesRegex(WindowEvidenceError, "inventory_incomplete"):
            audit_snapshot(snap, now=self.now())

    def test_inventory_complete_must_be_boolean(self):
        snap = self.snapshot([self.owner("pr:580")])
        snap["inventory_complete"] = 1
        with self.assertRaisesRegex(
            WindowEvidenceError,
            "inventory_complete_invalid",
        ):
            audit_snapshot(snap, now=self.now())

    def test_rejects_stale_and_future_evidence(self):
        stale = self.snapshot([self.owner("pr:580")])
        stale["observed_at"] = "2026-10-07T06:00:00Z"
        with self.assertRaisesRegex(WindowEvidenceError, "snapshot_stale"):
            audit_snapshot(stale, now=self.now())

        future = self.snapshot([self.owner("pr:580")])
        future["observed_at"] = "2026-10-07T07:02:00Z"
        with self.assertRaisesRegex(WindowEvidenceError, "snapshot_from_future"):
            audit_snapshot(future, now=self.now())

    def test_rejects_empty_owner_set_and_loose_types(self):
        with self.assertRaisesRegex(WindowEvidenceError, "owners_invalid"):
            audit_snapshot(self.snapshot([]), now=self.now())

        snap = self.snapshot([self.owner("pr:580")])
        snap["schema_version"] = True
        with self.assertRaisesRegex(
            WindowEvidenceError,
            "schema_version_unsupported",
        ):
            audit_snapshot(snap, now=self.now())

        with self.assertRaisesRegex(WindowEvidenceError, "max_age_invalid"):
            audit_snapshot(
                self.snapshot([self.owner("pr:580")]),
                now=self.now(),
                max_age_seconds=True,
            )

    def test_cli_require_clear_returns_distinct_blocked_exit(self):
        snap = self.snapshot([self.owner("pr:580")])
        snap["observed_at"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "window.json"
            path.write_text(json.dumps(snap), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(path),
                    "--require-clear",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(2, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual("blocked", payload["status"])
        self.assertEqual(["pr:580"], payload["active_owner_refs"])
        self.assertNotIn("title", completed.stdout)
        self.assertFalse(payload["mutation_performed"])

    def test_workflow_is_hosted_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        for forbidden in (
            "actions: write",
            "contents: write",
            "systemctl",
            "sudo ",
            "curl -X",
            "/api/",
            "sqlite3",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
