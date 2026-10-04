import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectAutonomyCoverageTests(unittest.TestCase):
    def setUp(self):
        self.projects = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        self.contracts = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))
        self.legacy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

    def test_every_registered_active_project_has_explicit_runtime_contract(self):
        explicit = set((self.contracts.get("projects") or {}).keys())
        active = {
            str(project["id"])
            for project in self.projects
            if str(project.get("status") or "active") != "archived"
        }
        self.assertEqual(
            active,
            explicit,
            "Every active project must declare one canonical project runtime contract.",
        )

    def test_registry_queue_and_lane_metadata_match_runtime_contract(self):
        contracts = self.contracts["projects"]
        for project in self.projects:
            pid = str(project["id"])
            if str(project.get("status") or "active") == "archived":
                continue
            contract = contracts[pid]
            self.assertEqual(project.get("queue_mode"), contract.get("queue_mode"), pid)
            self.assertEqual(project.get("lane_profile"), contract.get("lane_profile"), pid)

    def test_human_gated_projects_are_fail_closed(self):
        contracts = self.contracts["projects"]
        for project in self.projects:
            if str(project.get("queue_mode") or "").lower() != "human-gated":
                continue
            cfg = contracts[str(project["id"])]["autonomy"]
            compute = contracts[str(project["id"])]["compute"]
            pool = self.contracts["resource_pools"][compute["pool"]]
            self.assertIn(cfg.get("mode"), {"external_gate", "manual"})
            self.assertFalse(bool(cfg.get("auto_start")))
            self.assertEqual(0, pool.get("slots"))

    def test_server_has_no_hard_coded_project_worker_cap_table(self):
        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("PROJECT_AI_WORKER_LIMITS", server_source)
        self.assertIn("project_runtime.ai_worker_cap", server_source)

    def test_legacy_concise_prompt_promotion_lane_is_removed(self):
        self.assertFalse(
            (ROOT / ".github/workflows/zcloud-concise-worker-prompt-promotion.yml").exists(),
            "prompt/backend promotion must use the canonical guarded production deploy lane",
        )

    def test_legacy_autonomy_file_contains_no_runtime_truth(self):
        self.assertEqual(1, self.legacy.get("schema_version"))
        self.assertEqual({}, self.legacy.get("default"))
        self.assertEqual({}, self.legacy.get("projects"))
        self.assertIn(
            "compatibility",
            str(self.legacy.get("description") or "").lower(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
