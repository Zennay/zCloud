import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectAutonomyCoverageTests(unittest.TestCase):
    def test_every_registered_active_project_has_explicit_autonomy_contract(self):
        projects = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))
        explicit = set((policy.get("projects") or {}).keys())
        active = {
            str(project["id"])
            for project in projects
            if str(project.get("status") or "active") != "archived"
        }
        self.assertEqual(
            active,
            explicit,
            "Every active project must declare an explicit autonomy policy; "
            "new projects may not silently inherit the global default.",
        )

    def test_human_gated_projects_are_not_auto_started(self):
        projects = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))
        by_id = {str(project["id"]): project for project in projects}
        for project_id, project in by_id.items():
            if str(project.get("queue_mode") or "").lower() != "human-gated":
                continue
            cfg = (policy.get("projects") or {}).get(project_id) or {}
            self.assertIn(cfg.get("mode"), {"external_gate", "manual"})
            self.assertFalse(bool(cfg.get("auto_start")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
