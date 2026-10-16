"""Fail-closed Tuddy incubation contract: no VPS compute admission before APK gates."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class TuddyOnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        cls.contracts = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))
        cls.layout = json.loads((ROOT / "project-layout.json").read_text(encoding="utf-8"))

    def test_exactly_one_project(self):
        matches = [p for p in self.registry if p.get("id") == "tuddy"]
        self.assertEqual(1, len(matches))
        self.assertEqual("https://github.com/Zennay/Tuddy", matches[0]["repo_url"])
        self.assertEqual(
            "https://app.notion.com/p/3f89e19ac955810aa4a5e53871966f8f",
            matches[0]["notion_url"],
        )
        self.assertEqual("paused", matches[0]["status"])
        self.assertNotIn("repo", matches[0], "Incubating Android app must not be assigned VPS checkout")

    def test_no_unearned_apk_or_language_milestones(self):
        tuddy = next(p for p in self.registry if p["id"] == "tuddy")
        milestones = tuddy["milestones"]
        self.assertTrue(any("GitHub" in m["title"] and m["done"] for m in milestones))
        for m in milestones:
            if "GitHub" not in m["title"]:
                self.assertFalse(m["done"], m["title"])
                self.assertLess(m["progress"], 100)

    def test_no_worker_or_compute_admission(self):
        c = self.contracts["projects"]["tuddy"]
        self.assertEqual("human-gated", c["queue_mode"])
        self.assertEqual("human-gated", c["lane_profile"])
        self.assertEqual(0, c["ai_worker_cap"])
        self.assertEqual("external_gate", c["autonomy"]["mode"])
        self.assertIs(False, c["autonomy"]["auto_start"])
        self.assertEqual("disabled", c["compute"]["pool"])
        self.assertEqual(0, c["compute"]["cpu_soft_cores"])
        self.assertEqual(0, c["compute"]["memory_soft_mb"])
        self.assertEqual(0, self.contracts["resource_pools"]["disabled"]["slots"])

    def test_layout_once(self):
        self.assertEqual(1, self.layout["order"].count("tuddy"))

if __name__ == "__main__":
    unittest.main()
