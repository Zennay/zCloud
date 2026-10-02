import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_prechange_evidence as evidence


class PrechangeEvidenceTests(unittest.TestCase):
    def test_report_is_sanitized_and_tracks_exact_audit_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            state = base / "state"
            candidate = base / "candidate"
            snapshot = state / "snapshots" / "lkg-1"
            for path in (root, candidate, snapshot / "files"):
                path.mkdir(parents=True, exist_ok=True)

            (state / "last-known-good.json").write_text(
                json.dumps({"snapshot_id": "lkg-1"}), encoding="utf-8"
            )
            (snapshot / "manifest.json").write_text(
                json.dumps({"created_at": "2026-10-02T12:00:00+00:00"}),
                encoding="utf-8",
            )

            baseline_projects = [{"id": "cloud"}]
            live_projects = [{"id": "cloud"}, {"id": "ftmo"}]
            baseline_layout = {"order": ["cloud"], "archived": []}
            live_layout = {"order": ["ftmo", "cloud"], "archived": []}
            for rel, old, live in (
                ("projects.json", baseline_projects, live_projects),
                ("project-layout.json", baseline_layout, live_layout),
            ):
                (snapshot / "files" / rel).write_text(json.dumps(old), encoding="utf-8")
                (root / rel).write_text(json.dumps(live), encoding="utf-8")
                (candidate / rel).write_text(json.dumps(live), encoding="utf-8")

            for directory, value in (
                (snapshot / "files", "print('old')\n"),
                (root, "print('live')\n"),
                (candidate, "print('candidate')\n"),
            ):
                (directory / "server.py").write_text(value, encoding="utf-8")

            with sqlite3.connect(root / "history.db") as conn:
                conn.execute(
                    "CREATE TABLE config_audit("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                    "config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, "
                    "new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
                )
                conn.executemany(
                    "INSERT INTO config_audit(ts,actor,config_key,target,old_value_json,new_value_json,result,detail) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    [
                        (
                            "2026-10-02T13:00:00+00:00", "secret-actor",
                            "project.catalog", "portfolio", "[]",
                            json.dumps(live_projects), "succeeded", "sensitive detail",
                        ),
                        (
                            "2026-10-02T13:01:00+00:00", "secret-actor",
                            "project.layout", "portfolio", "{}",
                            json.dumps(live_layout), "succeeded", "sensitive detail",
                        ),
                    ],
                )

            report = evidence.build_report(root, state, candidate)
            self.assertTrue(report["files"]["projects.json"]["audits"][0]["matches_current"])
            self.assertTrue(report["files"]["projects.json"]["audits"][0]["after_lkg"])
            self.assertTrue(report["files"]["project-layout.json"]["live_equals_candidate"])
            rendered = json.dumps(report, sort_keys=True)
            self.assertNotIn("secret-actor", rendered)
            self.assertNotIn("sensitive detail", rendered)
            self.assertNotIn('"id": "ftmo"', rendered)
            self.assertIn("live_sha256", rendered)


if __name__ == "__main__":
    unittest.main()
