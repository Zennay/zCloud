import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_haxlab_telemetry_report",
    ROOT / "scripts" / "zcloud_haxlab_telemetry_report.py",
)
report = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(report)


class HaxLabTelemetryReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.autonomy = self.root / "autonomy-status.json"
        self.current = self.root / "current.json"
        self.live = self.root / "live.json"
        self.pipeline = self.root / "pipeline-summary.json"
        self.db = self.root / "haxlab.sqlite3"

        self.autonomy.write_text(
            json.dumps({
                "schema": "haxlab-autonomy-status-v2",
                "state": "RUNNING",
                "action": "train_generation",
                "generation": 12,
                "parent_champion": "gen-0011",
                "updated_at": "2026-10-06T16:00:00+00:00",
                "detail": "must never be exported",
            }),
            encoding="utf-8",
        )
        self.current.write_text(json.dumps({"version_id": "candidate-12", "secretish": "hidden"}), encoding="utf-8")
        self.live.write_text(
            json.dumps({"version_id": "champion-11", "live_health": {"healthy": True}, "raw": "hidden"}),
            encoding="utf-8",
        )
        self.pipeline.write_text(
            json.dumps({
                "best_epoch": 4,
                "selection": {
                    "selected_replays": 30,
                    "train_replay_count": 20,
                    "validation_replay_count": 5,
                    "holdout_replay_count": 5,
                },
                "live_test_gate": {
                    "eligible_for_live_test": True,
                    "reasons": ["bounded reason A", "bounded reason B"],
                },
                "frozen_holdout": {"samples": 400},
                "validation": {"samples": 300},
                "manifest_path": "/secret/raw/path",
            }),
            encoding="utf-8",
        )
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE replay_analysis(
                    sha256 TEXT, status TEXT, analyzer_version TEXT, output_path TEXT,
                    sampled_state_count INTEGER, player_count INTEGER,
                    raw_event_count INTEGER, tick_count INTEGER, error TEXT, updated_at TEXT
                );
                CREATE TABLE replay_processing(
                    sha256 TEXT, status TEXT, format_version INTEGER,
                    total_frames INTEGER, duration_seconds REAL,
                    decompressed_bytes INTEGER, parser_stage TEXT,
                    error TEXT, updated_at TEXT
                );
                """
            )
            conn.executemany(
                "INSERT INTO replay_processing VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    ("a", "ok", 1, 600, 10.0, 1000, "done", None, "x"),
                    ("b", "ok", 1, 1200, 20.0, 2000, "done", None, "x"),
                    ("c", "failed", 1, 0, 0.0, 0, "parse", "private error", "x"),
                ],
            )
            conn.executemany(
                "INSERT INTO replay_analysis VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    ("a", "ok", "v1", "/private/a", 10, 8, 100, 600, None, "x"),
                    ("b", "ok", "v1", "/private/b", 20, 8, 200, 1200, None, "x"),
                    ("c", "failed", "v1", "/private/c", 0, 0, 0, 0, "private error", "x"),
                ],
            )

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return report.build_snapshot(
            autonomy_path=self.autonomy,
            current_path=self.current,
            live_path=self.live,
            pipeline_path=self.pipeline,
            replay_db=self.db,
            allow_privileged_champion=False,
        )

    def test_projects_core_status_candidate_champion_gate_and_training(self):
        data = self.snapshot()
        self.assertTrue(data["available"])
        self.assertEqual("RUNNING", data["autonomy"]["state"])
        self.assertEqual("train_generation", data["autonomy"]["action"])
        self.assertEqual(12, data["autonomy"]["generation"])
        self.assertEqual("candidate-12", data["candidate"]["version_id"])
        self.assertEqual("champion-11", data["champion"]["version_id"])
        self.assertTrue(data["champion"]["healthy"])
        self.assertTrue(data["evaluation"]["eligible"])
        self.assertEqual(2, data["evaluation"]["reason_count"])
        self.assertEqual(400, data["evaluation"]["frozen_holdout_samples"])
        self.assertEqual(30, data["training"]["selected_replays"])
        self.assertEqual(20, data["training"]["train_replays"])

    def test_replay_throughput_uses_aggregate_read_only_metrics(self):
        data = self.snapshot()["replay"]
        self.assertTrue(data["available"])
        self.assertEqual({"failed": 1, "ok": 2}, data["processing_status_counts"])
        self.assertEqual({"failed": 1, "ok": 2}, data["analysis_status_counts"])
        self.assertEqual(30.0, data["processed_duration_seconds"])
        self.assertEqual(1800, data["processed_frames"])
        self.assertEqual(3000, data["processed_bytes"])
        self.assertEqual(30, data["sampled_states"])
        self.assertEqual(300, data["raw_events"])
        self.assertEqual(1800, data["analyzed_ticks"])

    def test_projection_does_not_leak_raw_detail_paths_reasons_or_errors(self):
        rendered = json.dumps(self.snapshot(), sort_keys=True)
        for secret in (
            "must never be exported",
            "/secret/raw/path",
            "bounded reason A",
            "bounded reason B",
            "/private/a",
            "/private/b",
            "/private/c",
            "private error",
            "secretish",
        ):
            self.assertNotIn(secret, rendered)

    def test_arena_v2_is_explicitly_not_inferred_from_offline_gate(self):
        data = self.snapshot()
        self.assertFalse(data["meta"]["arena_v2_integrated"])
        self.assertIn("do not infer", data["meta"]["arena_v2_note"])

    def test_wrong_autonomy_schema_fails_that_projection_closed(self):
        self.autonomy.write_text(
            json.dumps({"schema": "old", "state": "RUNNING", "action": "x"}),
            encoding="utf-8",
        )
        data = self.snapshot()
        self.assertFalse(data["autonomy"]["available"])

    def test_symlink_replay_db_is_rejected(self):
        real = self.db
        link = self.root / "linked.sqlite3"
        link.symlink_to(real)
        data = report.build_snapshot(
            autonomy_path=self.autonomy,
            current_path=self.current,
            live_path=self.live,
            pipeline_path=self.pipeline,
            replay_db=link,
            allow_privileged_champion=False,
        )
        self.assertFalse(data["replay"]["available"])
        self.assertEqual("symlink_refused", data["replay"]["error"])

    def test_privileged_read_is_allowlisted_to_canonical_champion_paths(self):
        self.assertEqual(
            {str(report.CURRENT_CHAMPION), str(report.LIVE_CHAMPION)},
            set(report.PRIVILEGED_JSON),
        )
        self.assertNotIn(str(report.AUTONOMY_STATUS), report.PRIVILEGED_JSON)
        self.assertNotIn(str(report.REPLAY_DB), report.PRIVILEGED_JSON)


if __name__ == "__main__":
    unittest.main()
