import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.zcloud_roadmap_owner_coverage import SnapshotError, audit_snapshot


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_roadmap_owner_coverage.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-roadmap-owner-coverage.yml"


class RoadmapOwnerCoverageTests(unittest.TestCase):
    def now(self):
        return datetime(2026, 10, 7, 6, 30, tzinfo=timezone.utc)

    def capability(
        self,
        capability_id,
        *,
        lane="control-plane",
        state="open",
        executable=True,
        dependency_ready=True,
        human_gate=False,
    ):
        return {
            "id": capability_id,
            "lane": lane,
            "state": state,
            "executable": executable,
            "dependency_ready": dependency_ready,
            "human_gate": human_gate,
        }

    def owner(self, capability_id, owner_id="914", *, kind="pr", state="active"):
        return {
            "capability_id": capability_id,
            "kind": kind,
            "id": owner_id,
            "state": state,
        }

    def snapshot(self, capabilities, owners=None):
        return {
            "schema_version": 1,
            "observed_at": "2026-10-07T06:25:00Z",
            "lane": "control-plane",
            "capabilities": capabilities,
            "owners": owners or [],
        }

    def test_reports_unowned_executable_capability_as_ready(self):
        result = audit_snapshot(
            self.snapshot(
                [
                    self.capability("actions-queue-pressure"),
                    self.capability("roadmap-owner-coverage"),
                ],
                [self.owner("actions-queue-pressure")],
            ),
            now=self.now(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])
        self.assertEqual(
            ["roadmap-owner-coverage"], result["candidate_capability_ids"]
        )
        self.assertEqual(["actions-queue-pressure"], result["owned_capability_ids"])

    def test_reports_saturated_when_every_eligible_capability_has_one_owner(self):
        result = audit_snapshot(
            self.snapshot(
                [self.capability("one"), self.capability("two")],
                [self.owner("one", "10"), self.owner("two", "11")],
            ),
            now=self.now(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("saturated", result["state"])
        self.assertEqual([], result["candidate_capability_ids"])

    def test_same_owner_can_cover_multiple_capabilities(self):
        result = audit_snapshot(
            self.snapshot(
                [self.capability("one"), self.capability("two")],
                [self.owner("one", "10"), self.owner("two", "10")],
            ),
            now=self.now(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("saturated", result["state"])
        self.assertEqual(["one", "two"], result["owned_capability_ids"])

    def test_duplicate_owner_row_for_same_capability_is_rejected(self):
        with self.assertRaisesRegex(SnapshotError, "owner_duplicate"):
            audit_snapshot(
                self.snapshot(
                    [self.capability("one")],
                    [self.owner("one", "10"), self.owner("one", "10")],
                ),
                now=self.now(),
            )

    def test_fails_visible_on_multiple_active_owners(self):
        result = audit_snapshot(
            self.snapshot(
                [self.capability("shared")],
                [
                    self.owner("shared", "10"),
                    self.owner("shared", "11", kind="issue"),
                ],
            ),
            now=self.now(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual("collision", result["state"])
        self.assertEqual(
            {"shared": ["issue:11", "pr:10"]},
            result["collisions"],
        )

    def test_excludes_dependency_human_gate_and_non_executable_work(self):
        result = audit_snapshot(
            self.snapshot(
                [
                    self.capability("blocked", dependency_ready=False),
                    self.capability("human", human_gate=True),
                    self.capability("docs", executable=False),
                    self.capability("done", state="complete"),
                    self.capability("other-lane", lane="deploy-ops"),
                ]
            ),
            now=self.now(),
        )
        self.assertEqual("idle", result["state"])
        self.assertEqual([], result["eligible_capability_ids"])

    def test_rejects_stale_snapshot(self):
        snap = self.snapshot([self.capability("one")])
        snap["observed_at"] = "2026-10-07T05:00:00Z"
        with self.assertRaisesRegex(SnapshotError, "snapshot_stale"):
            audit_snapshot(snap, now=self.now())

    def test_rejects_duplicate_capability(self):
        with self.assertRaisesRegex(SnapshotError, "capability_duplicate"):
            audit_snapshot(
                self.snapshot(
                    [self.capability("dup"), self.capability("dup")]
                ),
                now=self.now(),
            )

    def test_rejects_owner_for_unknown_capability(self):
        with self.assertRaisesRegex(SnapshotError, "owner_unknown_capability"):
            audit_snapshot(
                self.snapshot(
                    [self.capability("known")],
                    [self.owner("unknown")],
                ),
                now=self.now(),
            )

    def test_closed_owner_does_not_block_candidate(self):
        result = audit_snapshot(
            self.snapshot(
                [self.capability("free")],
                [self.owner("free", state="closed")],
            ),
            now=self.now(),
        )
        self.assertEqual("ready", result["state"])
        self.assertEqual(["free"], result["candidate_capability_ids"])

    def test_cli_require_candidate_fails_closed_when_saturated(self):
        snap = self.snapshot(
            [self.capability("owned")],
            [self.owner("owned")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(snap), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(path),
                    "--max-age-seconds",
                    "3600",
                    "--require-candidate",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(2, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual("saturated", payload["state"])

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
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
