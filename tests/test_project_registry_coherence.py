import copy
import json
import unittest
from pathlib import Path

from scripts.zcloud_project_registry_coherence import audit_project_registry


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class ProjectRegistryCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.projects = load("projects.json")
        self.contracts = load("project-contracts.json")
        self.vps_policy = load("vps-execution-policy.json")
        self.queue_seed = load("portfolio_queue.seed.json")

    def audit(self):
        return audit_project_registry(
            self.projects,
            self.contracts,
            self.vps_policy,
            self.queue_seed,
        )

    def test_repository_registration_surfaces_are_coherent(self):
        result = self.audit()
        self.assertEqual("coherent", result["state"], result)
        self.assertIn("zguard", result["autostart_execution_projects"])
        self.assertEqual([], result["errors"])

    def test_missing_runtime_contract_fails_closed(self):
        self.contracts = copy.deepcopy(self.contracts)
        self.contracts["projects"].pop("zguard")
        result = self.audit()
        self.assertEqual("incoherent", result["state"])
        self.assertIn(
            {"code": "missing_runtime_contract", "project_ids": ["zguard"]},
            result["errors"],
        )

    def test_missing_vps_policy_registration_fails_closed(self):
        self.vps_policy = copy.deepcopy(self.vps_policy)
        self.vps_policy["projects"].remove("zguard")
        result = self.audit()
        self.assertEqual("incoherent", result["state"])
        self.assertIn(
            {"code": "missing_vps_policy_registration", "project_ids": ["zguard"]},
            result["errors"],
        )

    def test_autostart_execution_project_requires_runnable_seed(self):
        self.queue_seed = [
            row for row in self.queue_seed if row.get("project_id") != "zguard"
        ]
        result = self.audit()
        self.assertEqual("incoherent", result["state"])
        self.assertIn(
            {"code": "autostart_project_without_runnable_seed", "project_ids": ["zguard"]},
            result["errors"],
        )

    def test_human_gated_project_does_not_require_queue_seed(self):
        self.queue_seed = [
            row for row in self.queue_seed if row.get("project_id") != "ulab"
        ]
        result = self.audit()
        self.assertNotIn(
            "ulab",
            {
                project_id
                for error in result["errors"]
                if error["code"] == "autostart_project_without_runnable_seed"
                for project_id in error["project_ids"]
            },
        )

    def test_unknown_queue_project_fails_closed(self):
        self.queue_seed = copy.deepcopy(self.queue_seed)
        self.queue_seed.append(
            {
                "queue_id": "unknown-project-test",
                "project_id": "ghost",
                "status": "queued",
                "eligible": True,
            }
        )
        result = self.audit()
        self.assertEqual("incoherent", result["state"])
        self.assertIn(
            {"code": "unknown_queue_project", "project_ids": ["ghost"]},
            result["errors"],
        )

    def test_duplicate_registry_project_fails_closed(self):
        self.projects = copy.deepcopy(self.projects)
        self.projects.append(copy.deepcopy(self.projects[0]))
        result = self.audit()
        self.assertEqual("incoherent", result["state"])
        self.assertEqual("duplicate_project_id", result["errors"][0]["code"])


if __name__ == "__main__":
    unittest.main()
