from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_worker_attention_projection as projection

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_worker_attention_projection.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-worker-attention-projection.yml"


def cycle(
    worker_id: str,
    cycle_id: str,
    start: dt.datetime,
    end: dt.datetime,
    category: str,
    *,
    vps_delegable: bool = False,
    material_outputs: list[str] | None = None,
) -> dict:
    return {
        "worker_id": worker_id,
        "cycle_id": cycle_id,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "category": category,
        "vps_delegable": vps_delegable,
        "material_outputs": material_outputs or [],
    }


def snapshot(now: dt.datetime) -> dict:
    base = now - dt.timedelta(minutes=60)
    return {
        "schema_version": 1,
        "captured_at": now.isoformat(),
        "cycles": [
            cycle(
                "cloud:w1",
                "c1",
                base,
                base + dt.timedelta(minutes=20),
                "feature_code",
                material_outputs=["commit", "code_change"],
            ),
            cycle(
                "cloud:w1",
                "c2",
                base + dt.timedelta(minutes=20),
                base + dt.timedelta(minutes=30),
                "validation",
                vps_delegable=True,
                material_outputs=["deploy_success"],
            ),
            cycle(
                "cloud:w1",
                "c3",
                base + dt.timedelta(minutes=30),
                base + dt.timedelta(minutes=40),
                "polling_waiting",
                vps_delegable=True,
            ),
            cycle(
                "cloud:w2",
                "c4",
                base + dt.timedelta(minutes=40),
                base + dt.timedelta(minutes=60),
                "bugfix",
                material_outputs=["blocker_removed", "code_change"],
            ),
        ],
    }


class WorkerAttentionProjectionTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 7, 10, 10, tzinfo=dt.timezone.utc)

    def test_projects_useful_validation_and_delegable_attention(self):
        result = projection.project_snapshot(snapshot(self.now), now=self.now)

        self.assertTrue(result["ok"])
        self.assertEqual(projection.POLICY, result["policy"])
        self.assertEqual(4, result["cycle_count"])
        self.assertEqual(2, result["worker_count"])
        self.assertEqual(3600, result["attention_seconds"])
        self.assertEqual(2400, result["useful_attention_seconds"])
        self.assertEqual(0.6667, result["useful_attention_ratio"])
        self.assertEqual(600, result["validation_attention_seconds"])
        self.assertEqual(16.67, result["validation_attention_percentage"])
        self.assertEqual(1200, result["vps_delegable_attention_seconds"])
        self.assertEqual(33.33, result["vps_delegable_attention_percentage"])
        self.assertFalse(result["mutation_performed"])

        w1 = next(item for item in result["workers"] if item["worker_id"] == "cloud:w1")
        self.assertEqual(3, w1["cycle_count"])
        self.assertEqual(1200, w1["useful_attention_seconds"])
        self.assertEqual(0.5, w1["useful_attention_ratio"])
        self.assertEqual(50.0, w1["vps_delegable_attention_percentage"])
        self.assertEqual(1, w1["by_category"]["feature_code"])
        self.assertEqual(1, w1["by_category"]["validation"])
        self.assertEqual(1, w1["by_category"]["polling_waiting"])
        self.assertEqual(1, w1["material_outputs"]["commit"])
        self.assertEqual(1, w1["material_outputs"]["deploy_success"])

    def test_delegable_cycle_emits_delegate_and_continue_advice(self):
        result = projection.project_snapshot(snapshot(self.now), now=self.now)
        by_id = {item["cycle_id"]: item for item in result["cycles"]}
        self.assertEqual("delegate_and_continue", by_id["c2"]["routing_advice"])
        self.assertEqual("delegate_and_continue", by_id["c3"]["routing_advice"])
        self.assertEqual("retain_worker_attention", by_id["c1"]["routing_advice"])
        self.assertEqual("retain_worker_attention", by_id["c4"]["routing_advice"])

    def test_non_deterministic_work_cannot_be_marked_vps_delegable(self):
        payload = snapshot(self.now)
        payload["cycles"][0]["vps_delegable"] = True
        with self.assertRaisesRegex(
            projection.AttentionError, "vps_delegable_category_invalid"
        ):
            projection.project_snapshot(payload, now=self.now)

        payload = snapshot(self.now)
        payload["cycles"][-1]["vps_delegable"] = True
        with self.assertRaisesRegex(
            projection.AttentionError, "vps_delegable_category_invalid"
        ):
            projection.project_snapshot(payload, now=self.now)

    def test_material_milestone_can_make_non_code_cycle_useful(self):
        payload = snapshot(self.now)
        payload["cycles"][2]["material_outputs"] = ["milestone_moved"]
        result = projection.project_snapshot(payload, now=self.now)
        c3 = next(item for item in result["cycles"] if item["cycle_id"] == "c3")
        self.assertTrue(c3["useful_attention"])
        self.assertEqual(3000, result["useful_attention_seconds"])
        self.assertEqual(0.8333, result["useful_attention_ratio"])

    def test_time_window_filters_cycles_without_mutating_source(self):
        payload = snapshot(self.now)
        start = self.now - dt.timedelta(minutes=30)
        end = self.now - dt.timedelta(minutes=10)
        result = projection.project_snapshot(
            payload,
            now=self.now,
            window_start=start.isoformat(),
            window_end=end.isoformat(),
        )
        self.assertEqual(["c2", "c3", "c4"], [item["cycle_id"] for item in result["cycles"]])
        self.assertEqual(start.isoformat(), result["window"]["start"])
        self.assertEqual(end.isoformat(), result["window"]["end"])

    def test_duplicate_cycle_identity_fails_closed(self):
        payload = snapshot(self.now)
        payload["cycles"].append(dict(payload["cycles"][0]))
        with self.assertRaisesRegex(projection.AttentionError, "cycle_duplicate"):
            projection.project_snapshot(payload, now=self.now)

    def test_unknown_keys_and_unbounded_free_text_are_rejected(self):
        payload = snapshot(self.now)
        payload["cycles"][0]["prompt"] = "raw prompt must never enter bounded telemetry"
        with self.assertRaisesRegex(projection.AttentionError, "cycle_keys_invalid"):
            projection.project_snapshot(payload, now=self.now)

        payload = snapshot(self.now)
        payload["raw_logs"] = ["secret"]
        with self.assertRaisesRegex(projection.AttentionError, "snapshot_keys_invalid"):
            projection.project_snapshot(payload, now=self.now)

    def test_bad_output_and_time_order_fail_closed(self):
        payload = snapshot(self.now)
        payload["cycles"][0]["material_outputs"] = ["arbitrary_text"]
        with self.assertRaisesRegex(projection.AttentionError, "material_output_invalid"):
            projection.project_snapshot(payload, now=self.now)

        payload = snapshot(self.now)
        payload["cycles"][0]["ended_at"] = (
            self.now - dt.timedelta(hours=2)
        ).isoformat()
        with self.assertRaisesRegex(projection.AttentionError, "cycle_time_order_invalid"):
            projection.project_snapshot(payload, now=self.now)

    def test_snapshot_file_rejects_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(
                projection.AttentionError, "snapshot_symlink_rejected"
            ):
                projection.load_snapshot(link)

            large = root / "large.json"
            large.write_text("x" * (projection.MAX_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(projection.AttentionError, "snapshot_too_large"):
                projection.load_snapshot(large)

    def test_cli_emits_only_bounded_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(snapshot(dt.datetime.now(dt.timezone.utc))), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(path), "--require-cycles"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            encoded = json.dumps(result, sort_keys=True)
            self.assertEqual(projection.POLICY, result["policy"])
            self.assertFalse(result["mutation_performed"])
            for forbidden in ("prompt", "conversation", "raw_logs", "comment", "token"):
                self.assertNotIn(forbidden, encoded.lower())

    def test_workflow_is_read_only_exact_head_and_permanent_vps_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("ZCLOUD_WORKER_ATTENTION_PROJECTION_GREEN=", text)
        self.assertIn("retention-days: 14", text)
        for forbidden in (
            "contents: write",
            "actions: write",
            "sudo ",
            "systemctl ",
            "git push",
            "sqlite3 ",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
