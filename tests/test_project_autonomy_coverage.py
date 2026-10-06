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

    def test_registry_contains_no_runtime_queue_or_lane_truth(self):
        for project in self.projects:
            if str(project.get("status") or "active") == "archived":
                continue
            self.assertNotIn("queue_mode", project, project["id"])
            self.assertNotIn("lane_profile", project, project["id"])

    def test_server_hydrates_registry_runtime_fields_from_canonical_contract(self):
        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("def _load_project_registry():", server_source)
        self.assertIn("contract=project_runtime.project_contract(project_id)", server_source)
        self.assertIn("project['queue_mode']=str(contract.get('queue_mode') or '')", server_source)
        self.assertIn("project['lane_profile']=str(contract.get('lane_profile') or '')", server_source)
        self.assertIn("projects = _load_project_registry()", server_source)

    def test_human_gated_projects_are_fail_closed(self):
        contracts = self.contracts["projects"]
        for pid, contract in contracts.items():
            if str(contract.get("queue_mode") or "").lower() != "human-gated":
                continue
            cfg = contract["autonomy"]
            compute = contract["compute"]
            pool = self.contracts["resource_pools"][compute["pool"]]
            self.assertIn(cfg.get("mode"), {"external_gate", "manual"}, pid)
            self.assertFalse(bool(cfg.get("auto_start")), pid)
            self.assertEqual(0, pool.get("slots"), pid)

    def test_zssh_external_gate_pauses_ai_without_disabling_control_plane(self):
        contract = self.contracts["projects"]["zssh"]
        self.assertEqual("execution", contract["queue_mode"])
        self.assertEqual("external_gate", contract["autonomy"]["mode"])
        self.assertFalse(contract["autonomy"]["auto_start"])
        self.assertEqual("protected", contract["compute"]["pool"])
        self.assertTrue(contract["compute"]["protected"])

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
