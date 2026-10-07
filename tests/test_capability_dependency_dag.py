import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.zcloud_capability_dependency_dag import SnapshotError, audit_snapshot


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_capability_dependency_dag.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-capability-dependency-dag.yml"


class CapabilityDependencyDagTests(unittest.TestCase):
    def now(self):
        return datetime(2026, 10, 7, 6, 40, tzinfo=timezone.utc)

    def capability(self, capability_id, *, lane="control-plane", state="open", dependencies=None):
        return {
            "id": capability_id,
            "lane": lane,
            "state": state,
            "dependencies": dependencies or [],
        }

    def snapshot(self, capabilities):
        return {
            "schema_version": 1,
            "observed_at": "2026-10-07T06:35:00Z",
            "lane": "control-plane",
            "capabilities": capabilities,
        }

    def test_marks_capability_ready_only_when_all_dependencies_complete(self):
        result = audit_snapshot(
            self.snapshot([
                self.capability("foundation", state="complete"),
                self.capability("consumer", dependencies=["foundation"]),
            ]),
            now=self.now(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])
        self.assertEqual(["consumer"], result["ready_capability_ids"])
        self.assertEqual({}, result["dependency_gaps"])

    def test_waits_on_open_dependency(self):
        result = audit_snapshot(
            self.snapshot([
                self.capability("foundation"),
                self.capability("consumer", dependencies=["foundation"]),
            ]),
            now=self.now(),
        )
        self.assertEqual("ready", result["state"])
        self.assertEqual(["foundation"], result["ready_capability_ids"])
        self.assertEqual(["consumer"], result["waiting_capability_ids"])
        self.assertEqual({"consumer": ["foundation"]}, result["dependency_gaps"])

    def test_cancelled_dependency_blocks_dependent(self):
        result = audit_snapshot(
            self.snapshot([
                self.capability("retired", state="cancelled"),
                self.capability("consumer", dependencies=["retired"]),
            ]),
            now=self.now(),
        )
        self.assertEqual("blocked", result["state"])
        self.assertEqual(["consumer"], result["blocked_capability_ids"])
        self.assertEqual({"consumer": ["retired"]}, result["dependency_gaps"])

    def test_cross_lane_dependency_is_valid(self):
        result = audit_snapshot(
            self.snapshot([
                self.capability("deploy-gate", lane="deploy-ops", state="complete"),
                self.capability("consumer", dependencies=["deploy-gate"]),
            ]),
            now=self.now(),
        )
        self.assertEqual(["consumer"], result["ready_capability_ids"])

    def test_cycle_is_fail_visible(self):
        result = audit_snapshot(
            self.snapshot([
                self.capability("one", dependencies=["two"]),
                self.capability("two", dependencies=["one"]),
            ]),
            now=self.now(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual("cycle", result["state"])
        self.assertEqual(["one", "two", "one"], result["cycle"])

    def test_rejects_unknown_self_and_duplicate_dependencies(self):
        cases = [
            ([self.capability("one", dependencies=["missing"])], "dependency_unknown"),
            ([self.capability("one", dependencies=["one"])], "self_dependency"),
            (
                [
                    self.capability("base", state="complete"),
                    self.capability("one", dependencies=["base", "base"]),
                ],
                "dependency_duplicate",
            ),
        ]
        for rows, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(SnapshotError, code):
                    audit_snapshot(self.snapshot(rows), now=self.now())

    def test_rejects_stale_and_malformed_snapshot_types(self):
        stale = self.snapshot([self.capability("one")])
        stale["observed_at"] = "2026-10-07T05:00:00Z"
        with self.assertRaisesRegex(SnapshotError, "snapshot_stale"):
            audit_snapshot(stale, now=self.now())

        malformed = self.snapshot([self.capability("one")])
        malformed["capabilities"][0]["state"] = []
        with self.assertRaisesRegex(SnapshotError, "capability_state_invalid"):
            audit_snapshot(malformed, now=self.now())

        bad_version = self.snapshot([self.capability("one")])
        bad_version["schema_version"] = True
        with self.assertRaisesRegex(SnapshotError, "schema_version_unsupported"):
            audit_snapshot(bad_version, now=self.now())

    def test_cli_require_ready_fails_closed_when_only_waiting(self):
        snap = self.snapshot([
            self.capability("upstream", lane="deploy-ops"),
            self.capability("consumer", dependencies=["upstream"]),
        ])
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
                    "--require-ready",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(2, completed.returncode)
        self.assertEqual("waiting", json.loads(completed.stdout)["state"])

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
        for forbidden in ("actions: write", "contents: write", "systemctl", "sudo ", "curl -X"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
