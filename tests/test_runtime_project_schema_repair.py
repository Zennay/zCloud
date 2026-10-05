import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "runtime_schema_repair", ROOT / "scripts" / "zcloud_runtime_project_schema_repair.py"
)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class RuntimeProjectSchemaRepairTests(unittest.TestCase):
    def test_repair_projects_only_fills_missing_or_blank_revision(self):
        source = [
            {"id": "cloud", "milestone_revision": "v4"},
            {"id": "lightup", "milestone_revision": ""},
            {"id": "ulab"},
        ]
        result, changed = repair.repair_projects(source)
        self.assertEqual(["lightup", "ulab"], changed)
        self.assertEqual("v4", result[0]["milestone_revision"])
        self.assertEqual("runtime-import-v1", result[1]["milestone_revision"])
        self.assertEqual("runtime-import-v1", result[2]["milestone_revision"])
        self.assertEqual("", source[1]["milestone_revision"])
        self.assertNotIn("milestone_revision", source[2])

    def test_repair_projects_adds_missing_candidate_without_overwriting_live(self):
        source = [{
            "id": "cloud",
            "milestone_revision": "live-v1",
            "phase": "runtime-owned phase",
        }]
        candidate = [
            {
                "id": "cloud",
                "milestone_revision": "candidate-v2",
                "phase": "candidate phase",
            },
            {
                "id": "zguard",
                "milestone_revision": "zguard-v1",
                "phase": "portfolio onboarding",
            },
        ]

        result, changed = repair.repair_projects(source, candidate)

        self.assertEqual(["zguard"], changed)
        self.assertEqual("runtime-owned phase", result[0]["phase"])
        self.assertEqual("live-v1", result[0]["milestone_revision"])
        self.assertEqual("zguard", result[1]["id"])
        self.assertEqual("portfolio onboarding", result[1]["phase"])
        self.assertEqual(1, len(source))

    def test_live_repair_is_atomic_audited_and_noops_after_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "live"
            candidate = Path(td) / "candidate"
            root.mkdir()
            (candidate / "scripts").mkdir(parents=True)
            projects = [{
                "id": "lightup",
                "name": "LightUp",
                "status": "active",
                "milestone_revision": "",
                "progress_basis": "runtime evidence",
                "milestones": [{"title": "M1", "done": False, "progress": 10}],
            }]
            (root / "projects.json").write_text(json.dumps(projects) + "\n", encoding="utf-8")
            candidate_projects = [
                dict(projects[0], milestone_revision="candidate-lightup-v2"),
                {
                    "id": "zguard",
                    "name": "zGuard",
                    "status": "active",
                    "milestone_revision": "zguard-v1",
                    "progress_basis": "runtime evidence",
                    "milestones": [{"title": "M0", "done": False, "progress": 10}],
                },
            ]
            (candidate / "projects.json").write_text(
                json.dumps(candidate_projects) + "\n",
                encoding="utf-8",
            )
            for name, value in (
                ("project-layout.json", {"order": ["lightup"], "archived": []}),
                ("resource-policy.json", {}),
            ):
                (root / name).write_text(json.dumps(value) + "\n", encoding="utf-8")
            (candidate / "server.py").write_text("MAX_CHATGPT_WORKERS = 3\n", encoding="utf-8")
            (candidate / "enhancements.py").write_text(
                "PRIORITY_WEIGHTS={'normal':1}\nPROJECT_UNITS={'lightup':[]}\n",
                encoding="utf-8",
            )
            validator = candidate / "scripts" / "validator.py"
            validator.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            db = root / "history.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE config_audit("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                    "config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, "
                    "new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
                )

            first = repair.repair_live(
                root=root,
                candidate=candidate,
                db=db,
                validator=validator,
                lock_path=Path(td) / "repair.lock",
            )
            self.assertTrue(first["changed"])
            self.assertEqual(["lightup", "zguard"], first["projects"])
            live = json.loads((root / "projects.json").read_text(encoding="utf-8"))
            self.assertEqual("runtime-import-v1", live[0]["milestone_revision"])
            self.assertEqual("active", live[0]["status"])
            self.assertEqual("zguard", live[1]["id"])
            self.assertEqual("zguard-v1", live[1]["milestone_revision"])
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT actor,config_key,target,result,new_value_json FROM config_audit"
                ).fetchone()
            self.assertEqual(
                ("github-actions-vps-deploy", "project.catalog", "portfolio", "succeeded"),
                row[:4],
            )
            self.assertEqual(live, json.loads(row[4]))

            second = repair.repair_live(
                root=root,
                candidate=candidate,
                db=db,
                validator=validator,
                lock_path=Path(td) / "repair.lock",
            )
            self.assertFalse(second["changed"])
            with sqlite3.connect(db) as conn:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM config_audit").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
