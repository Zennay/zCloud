"""TradeEngine portfolio onboarding must remain independent, fully wired and safe."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TradeEngineOnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projects = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
        cls.contracts = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))
        cls.layout = json.loads((ROOT / "project-layout.json").read_text(encoding="utf-8"))
        cls.policy = json.loads((ROOT / "resource-policy.json").read_text(encoding="utf-8"))
        cls.seed = json.loads((ROOT / "portfolio_queue.seed.json").read_text(encoding="utf-8"))

    def test_complete_portfolio_registration(self):
        matching = [p for p in self.projects if p.get("id") == "tradengine"]
        self.assertEqual(1, len(matching))
        project = matching[0]
        self.assertEqual("https://github.com/Zennay/tradengine", project["repo_url"])
        self.assertIn("3fa9e19ac95581fcb67ecc956eee60d6", project["notion_url"])
        self.assertEqual("active", project["status"])
        self.assertEqual(1, self.layout["order"].count("tradengine"))
        self.assertNotIn("tradengine", self.layout["archived"])
        self.assertEqual("background", self.policy["tradengine"]["priority"])
        self.assertEqual("background", self.contracts["projects"]["tradengine"]["compute"]["priority"])

    def test_independent_safe_runtime_contract(self):
        trad = self.contracts["projects"]["tradengine"]
        ftmo = self.contracts["projects"]["ftmo"]
        self.assertEqual("execution", trad["queue_mode"])
        self.assertEqual("research-validation", trad["lane_profile"])
        self.assertEqual(1, trad["ai_worker_cap"])
        self.assertEqual({"mode": "ai_worker", "auto_start": True}, trad["autonomy"])
        self.assertEqual("build", trad["compute"]["pool"])
        self.assertNotEqual(ftmo["compute"]["pool"], trad["compute"]["pool"])
        self.assertFalse(trad["compute"]["protected"])

    def test_initial_queue_is_scoped_and_non_trading(self):
        records = [i for i in self.seed if i.get("project_id") == "tradengine"]
        self.assertEqual(1, len(records))
        task = records[0]
        self.assertEqual("queued", task["status"])
        self.assertTrue(task["eligible"])
        self.assertEqual("P2", task["priority"])
        criteria = task["completion_criteria"].lower()
        self.assertIn("point-in-time", criteria)
        self.assertIn("no real orders", criteria)
        self.assertIn("no", criteria)
        self.assertNotEqual(
            [t.get("queue_id") for t in self.seed if t.get("project_id") == "ftmo"],
            [task["queue_id"]],
        )

    def test_priority_control_has_safe_unmanaged_entry(self):
        source = (ROOT / "enhancements.py").read_text(encoding="utf-8")
        self.assertIn('"tradengine": []', source)
        self.assertNotIn("tradengine.service", source)


if __name__ == "__main__":
    unittest.main()
