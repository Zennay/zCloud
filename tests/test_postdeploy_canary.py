import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_postdeploy_canary.py"
SPEC = importlib.util.spec_from_file_location("zcloud_postdeploy_canary", MODULE_PATH)
canary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(canary)


class PostdeployCanaryTests(unittest.TestCase):
    def sample(self):
        status = {
            "errors": [],
            "chatgpt_firefox": {"state": "active", "active": True},
            "chatgpt_runners": {
                "cloud": {
                    "workers": [],
                    "desired_worker_count": 0,
                    "active_worker_count": 0,
                }
            },
            "incidents": {"items": [], "count": 0},
        }
        targets = {"projects": {"cloud::w1": {}}, "max_workers": 8}
        mapping = {"available": True, "sha256": "abc", "targets": 1, "workers": 1}
        return status, targets, mapping

    def evaluate(self, **kwargs):
        status, targets, mapping = self.sample()
        return canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
            **kwargs,
        )

    def test_green_baseline(self):
        self.assertTrue(self.evaluate()["ok"])

    def test_blocks_service_failure(self):
        status, targets, mapping = self.sample()
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": False, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])

    def test_blocks_status_errors(self):
        status, targets, mapping = self.sample()
        status["errors"] = ["boom"]
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
        )
        self.assertFalse(result["ok"])

    def test_mapping_expectation_is_fail_closed(self):
        result = self.evaluate(expected_mapping_sha="different")
        checks = {x["name"]: x for x in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertFalse(checks["mapping_unchanged"]["ok"])

    def test_worker_read_model_can_be_required(self):
        self.assertTrue(self.evaluate(require_worker_read_model=True)["ok"])
        status, targets, mapping = self.sample()
        del status["chatgpt_runners"]["cloud"]["workers"]
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
            require_worker_read_model=True,
        )
        self.assertFalse(result["ok"])

    def test_incident_center_can_be_required(self):
        self.assertTrue(self.evaluate(require_incidents=True)["ok"])
        status, targets, mapping = self.sample()
        status.pop("incidents")
        result = canary.evaluate(
            status,
            targets,
            mapping,
            services={"zcloud": True, "firefox": True},
            static_assets_ok=True,
            source_runtime_match=True,
            require_incidents=True,
        )
        self.assertFalse(result["ok"])

    def test_mapping_fingerprint_matches_recovery_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "history.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE runner_targets("
                    "project_id TEXT, active INTEGER, worker_count INTEGER, conversation_id TEXT)"
                )
                conn.execute(
                    "CREATE TABLE runner_workers("
                    "project_id TEXT, worker_slot INTEGER, conversation_id TEXT)"
                )
                conn.execute(
                    "INSERT INTO runner_targets VALUES('cloud',1,2,'conversation-a')"
                )
                conn.execute(
                    "INSERT INTO runner_workers VALUES('cloud',1,'conversation-a')"
                )
                conn.execute(
                    "INSERT INTO runner_workers VALUES('cloud',2,'conversation-b')"
                )
            result = canary.mapping_fingerprint(db)
            payload = json.dumps({
                "targets": [{
                    "project_id": "cloud",
                    "active": 1,
                    "worker_count": 2,
                    "conversation_id": "conversation-a",
                }],
                "workers": [
                    {"project_id": "cloud", "worker_slot": 1, "conversation_id": "conversation-a"},
                    {"project_id": "cloud", "worker_slot": 2, "conversation_id": "conversation-b"},
                ],
            }, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
