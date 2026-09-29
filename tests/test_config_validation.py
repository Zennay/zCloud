import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_config_validate.py"
SPEC = importlib.util.spec_from_file_location("zcloud_config_validate", MODULE_PATH)
cfg = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(cfg)


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-config-")
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects.json"
        self.layout = self.root / "project-layout.json"
        self.resources = self.root / "resource-policy.json"
        self.server = self.root / "server.py"
        self.enhancements = self.root / "enhancements.py"
        self.db = self.root / "history.db"
        self.projects.write_text(json.dumps([{
            "id": "cloud",
            "name": "zCloud",
            "status": "active",
            "milestone_revision": "v1",
            "progress_basis": "checkpoint evidence",
            "priority": "system",
            "milestones": [
                {"title": "Core", "done": True, "progress": 100},
                {"title": "Next", "done": False, "progress": 25},
            ],
        }]))
        self.layout.write_text(json.dumps({"order": ["cloud"], "archived": []}))
        self.resources.write_text(json.dumps({"cloud": {"priority": "normal"}}))
        self.server.write_text("MAX_CHATGPT_WORKERS = 8\n")
        self.enhancements.write_text(
            "PRIORITY_WEIGHTS = {'background':100,'normal':400,'high':800,'turbo':3000}\n"
            "PROJECT_UNITS = {'cloud':['zennay-cloud.service']}\n"
        )
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE runner_targets(project_id TEXT, worker_count INTEGER)")
            conn.execute("CREATE TABLE runner_workers(project_id TEXT, worker_slot INTEGER, desired_state TEXT)")
            conn.execute("INSERT INTO runner_targets VALUES('cloud',1)")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',1,'running')")
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self):
        return cfg.validate(
            projects_path=self.projects,
            layout_path=self.layout,
            resource_path=self.resources,
            server_path=self.server,
            enhancements_path=self.enhancements,
            db_path=self.db,
        )

    def test_green_contract(self):
        result = self.validate()
        self.assertTrue(result["ok"], result)
        self.assertEqual(8, result["contracts"]["max_workers"])

    def test_blocks_duplicate_project_id_and_bad_progress(self):
        data = json.loads(self.projects.read_text())
        data[0]["milestones"][0]["progress"] = 101
        data.append(dict(data[0]))
        self.projects.write_text(json.dumps(data))
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("duplicate project id" in x for x in result["errors"]))
        self.assertTrue(any("0..100" in x for x in result["errors"]))

    def test_blocks_unknown_layout_reference(self):
        self.layout.write_text(json.dumps({"order": ["cloud", "ghost"], "archived": []}))
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("unknown project ids" in x for x in result["errors"]))

    def test_blocks_invalid_resource_priority_and_unsupported_key(self):
        self.resources.write_text(json.dumps({"cloud": {"priority": "warp", "cpu": 100}}))
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("priority" in x for x in result["errors"]))
        self.assertTrue(any("unsupported keys" in x for x in result["errors"]))

    def test_blocks_resource_policy_for_unmanaged_project(self):
        data = json.loads(self.projects.read_text())
        data.append({
            "id": "ulab",
            "name": "uLab",
            "status": "active",
            "milestone_revision": "v1",
            "progress_basis": "checkpoints",
            "priority": "normal",
            "milestones": [{"title": "Core", "done": False, "progress": 50}],
        })
        self.projects.write_text(json.dumps(data))
        self.resources.write_text(json.dumps({"ulab": {"priority": "high"}}))
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("no resource-control contract" in x for x in result["errors"]))

    def test_allows_inactive_legacy_worker_slot(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runner_workers VALUES('cloud',9,'paused')")
            conn.commit()
        result = self.validate()
        self.assertTrue(result["ok"], result)

    def test_blocks_worker_count_over_runtime_max(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE runner_targets SET worker_count=9 WHERE project_id='cloud'")
            conn.commit()
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("worker_count out of range" in x for x in result["errors"]))

    def test_blocks_invalid_worker_desired_state(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE runner_workers SET desired_state='broken'")
            conn.commit()
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("invalid desired_state" in x for x in result["errors"]))

    def test_runtime_contract_must_remain_literal(self):
        self.server.write_text("MAX_CHATGPT_WORKERS = int('8')\n")
        result = self.validate()
        self.assertFalse(result["ok"])
        self.assertTrue(any("must remain a literal" in x for x in result["errors"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
