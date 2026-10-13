"""Protect MixLab's existing Notion/GitHub onboarding and bounded zCloud admission."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HQ = "https://app.notion.com/p/3e79e19ac95581a78ee3f24aa5e7e8b6"
HANDOFF = "https://app.notion.com/p/3e79e19ac95581f4bf15c26a833da642"
REPO = "https://github.com/Zennay/Mixlab"


class MixLabPortfolioRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projects = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        cls.contracts = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))
        cls.resources = json.loads((ROOT / "resource-policy.json").read_text(encoding="utf-8"))
        cls.layout = json.loads((ROOT / "project-layout.json").read_text(encoding="utf-8"))

    def test_existing_project_identity_is_registered_exactly_once(self):
        self.assertEqual(1, sum(p["id"] == "mixlab" for p in self.projects))
        item = next(p for p in self.projects if p["id"] == "mixlab")
        self.assertEqual("MixLab", item["name"])
        self.assertEqual(REPO, item["repo_url"])
        self.assertEqual(HQ, item["notion_url"])
        self.assertEqual(HANDOFF, item["handoff_url"])
        self.assertEqual("active", item["status"])
        self.assertEqual("low", item["priority"])
        self.assertTrue(item["milestone_revision"])
        self.assertIn("blind", item["progress_basis"].lower())
        self.assertTrue(any("M1" in m["title"] and not m["done"] for m in item["milestones"]))
        self.assertNotIn("queue_mode", item)
        self.assertNotIn("lane_profile", item)

    def test_runtime_is_one_worker_and_never_autostarts_from_registration(self):
        registered = {p["id"] for p in self.projects if p.get("status") != "archived"}
        self.assertEqual(registered, set(self.contracts["projects"]))
        contract = self.contracts["projects"]["mixlab"]
        self.assertEqual("execution", contract["queue_mode"])
        self.assertEqual("product", contract["lane_profile"])
        self.assertEqual(1, contract["ai_worker_cap"])
        self.assertEqual({"mode": "ai_worker", "auto_start": False}, contract["autonomy"])
        compute = contract["compute"]
        self.assertEqual("build", compute["pool"])
        self.assertFalse(compute["protected"])
        self.assertEqual(1, compute["cpu_soft_cores"])
        self.assertEqual(512, compute["memory_soft_mb"])
        self.assertEqual("background", compute["priority"])
        self.assertEqual("background", self.resources["mixlab"]["priority"])

    def test_mixlab_is_visible_but_does_not_take_over_other_project_order(self):
        self.assertEqual(1, self.layout["order"].count("mixlab"))
        self.assertNotIn("mixlab", self.layout["archived"])
        self.assertEqual("mixlab", self.layout["order"][-1])
        self.assertEqual(len(self.layout["order"]), len(set(self.layout["order"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
