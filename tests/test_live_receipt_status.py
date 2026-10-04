import sqlite3
import tempfile
import unittest
from pathlib import Path

import project_runtime
import server


class LiveReceiptStatusTests(unittest.TestCase):
    def setUp(self):
        self._old_db = server.DB
        self.temp = tempfile.TemporaryDirectory()
        server.DB = Path(self.temp.name) / "history.db"
        with sqlite3.connect(server.DB) as conn:
            conn.row_factory = sqlite3.Row
            project_runtime.init_tables(conn)

    def tearDown(self):
        server.DB = self._old_db
        self.temp.cleanup()

    def cached_status(self):
        return {
            "state_receipt_coverage": {"ready": False, "missing": ["cloud"]},
            "projects": [{
                "id": "cloud",
                "status": "active",
                "phase": "cached phase",
                "next_step": "cached gate",
                "state_source": "registry",
                "execution_state": {"available": False},
            }],
        }

    def test_live_status_reflects_receipt_written_after_cached_sample(self):
        cached = self.cached_status()
        with sqlite3.connect(server.DB) as conn:
            conn.row_factory = sqlite3.Row
            receipt = project_runtime.record_receipt(
                conn,
                "cloud",
                phase="Runtime control-plane rollout",
                action="Guarded VPS production deploy succeeded",
                commit_sha="a4ab4163",
                ci_status="success",
                next_gate="Receipt coverage audit",
                source="github-actions:zcloud-vps-deploy",
                evidence={"production_status": "HEALTH_GREEN+POSTDEPLOY_GREEN"},
            )
            conn.commit()

        live = server.live_receipt_status(cached)
        project = live["projects"][0]

        self.assertEqual("registry", cached["projects"][0]["state_source"])
        self.assertEqual("evidence_receipt", project["state_source"])
        self.assertEqual(receipt["id"], project["execution_state"]["receipt_id"])
        self.assertEqual("a4ab4163", project["execution_state"]["commit_sha"])
        self.assertEqual("Runtime control-plane rollout", project["phase"])
        self.assertEqual("Receipt coverage audit", project["next_step"])
        self.assertTrue(live["state_receipt_coverage"]["ready"])
        self.assertEqual(["cloud"], live["state_receipt_coverage"]["current"])

    def test_malformed_latest_receipt_fails_closed_to_registry(self):
        cached = self.cached_status()
        cached["projects"][0]["state_source"] = "evidence_receipt"
        cached["projects"][0]["execution_state"] = {"available": True, "receipt_id": 1}
        with sqlite3.connect(server.DB) as conn:
            project_runtime.init_tables(conn)
            conn.execute(
                """INSERT INTO project_state_receipts(
                    project_id,phase,action,commit_sha,ci_status,blocker,next_gate,source,
                    observed_at,evidence_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "cloud","bad","bad","deadbeef","success","","bad","test",
                    "2026-10-04T20:00:00+00:00","[]","2026-10-04T20:00:00+00:00",
                ),
            )
            conn.commit()

        live = server.live_receipt_status(cached)
        project = live["projects"][0]

        self.assertEqual("registry", project["state_source"])
        self.assertFalse(project["execution_state"]["available"])
        self.assertFalse(live["state_receipt_coverage"]["ready"])
        self.assertEqual(["cloud"], live["state_receipt_coverage"]["invalid"])


if __name__ == "__main__":
    unittest.main()
