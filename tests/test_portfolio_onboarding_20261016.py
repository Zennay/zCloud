"""Guard the user-requested MixLab, Fuckball, Tuddy and existing TradeEngine registration."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_PROJECTS = {
    "mixlab": ("https://github.com/Zennay/Mixlab", "3e79e19ac95581a78ee3f24aa5e7e8b6"),
    "fuckball": ("https://github.com/Zennay/fuckball", "3f99e19ac95581bca947da243bd78f91"),
    "tuddy": ("https://github.com/Zennay/Tuddy", "3f89e19ac955810aa4a5e53871966f8f"),
}


def load(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class PortfolioOnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projects = load("projects.json")
        cls.worker_modes = load("worker-modes.json")
        cls.contracts = load("project-contracts.json")["projects"]
        cls.layout = load("project-layout.json")
        cls.resources = load("resource-policy.json")
        tree = ast.parse((ROOT / "enhancements.py").read_text(encoding="utf-8"))
        cls.units = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "PROJECT_UNITS" for t in node.targets)
        )

    def test_exactly_once_registered_with_real_sources(self):
        ids = [project["id"] for project in self.projects]
        self.assertEqual(len(ids), len(set(ids)))
        for pid, (repo, notion_id) in NEW_PROJECTS.items():
            with self.subTest(project=pid):
                self.assertEqual(ids.count(pid), 1)
                entry = next(p for p in self.projects if p["id"] == pid)
                self.assertEqual(entry["repo_url"], repo)
                self.assertIn(notion_id, entry["notion_url"])
                self.assertEqual(entry["status"], "active")
                self.assertTrue(entry["milestones"])
                self.assertNotIn("queue_mode", entry)
                self.assertNotIn("lane_profile", entry)

    def test_standalone_trade_engine_is_not_duplicated_or_modified(self):
        self.assertEqual(
            [p["id"] for p in self.projects].count("tradengine"), 1
        )
        self.assertEqual(
            next(p["repo_url"] for p in self.projects if p["id"] == "tradengine"),
            "https://github.com/Zennay/tradengine",
        )
        self.assertEqual(self.contracts["tradengine"]["autonomy"]["mode"], "ai_worker")

    def test_new_projects_are_gated_and_bounded(self):
        active_ids = {p["id"] for p in self.projects if p["status"] != "archived"}
        self.assertEqual(active_ids, set(self.contracts) - {mode["id"] for mode in self.worker_modes})
        for pid in NEW_PROJECTS:
            with self.subTest(project=pid):
                contract = self.contracts[pid]
                self.assertEqual(contract["ai_worker_cap"], 1)
                self.assertEqual(contract["autonomy"], {"mode": "external_gate", "auto_start": False})
                self.assertEqual(contract["queue_mode"], "execution")
                self.assertEqual(contract["compute"]["pool"], "build")
                self.assertEqual(contract["compute"]["priority"], "background")
                self.assertFalse(contract["compute"]["protected"])
                self.assertEqual(self.resources[pid], {"priority": "background"})
                self.assertEqual(self.units[pid], [])
                self.assertEqual(self.layout["order"].count(pid), 1)
                self.assertNotIn(pid, self.layout["archived"])

    def test_zsentinel_is_cross_project_worker_mode_not_project_card(self):
        project_ids = [p["id"] for p in self.projects]
        self.assertNotIn("zsentinel", project_ids)
        self.assertNotIn("zsentinel", self.layout["order"])
        self.assertNotIn("zsentinel", self.layout["archived"])
        self.assertEqual([m["id"] for m in self.worker_modes].count("zsentinel"), 1)
        self.assertEqual(self.worker_modes[0]["name"], "zSentinel · Vliegende keep")
        contract = self.contracts["zsentinel"]
        self.assertEqual(contract["ai_worker_cap"], 1)
        self.assertEqual(contract["autonomy"], {"mode": "ai_worker", "auto_start": True})
        self.assertEqual(contract["queue_mode"], "execution")
        self.assertEqual(self.resources["zsentinel"], {"priority": "high"})
        self.assertIn("zsentinel", load("vps-execution-policy.json")["projects"])
        self.assertEqual(self.units["zsentinel"], [])
        jobs = [j for j in load("portfolio_queue.seed.json") if j["project_id"] == "zsentinel"]
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["eligible"])
        self.assertIn("ALL projects", jobs[0]["completion_criteria"])

    def test_execution_routing_inventory_does_not_autostart_projects(self):
        # Required project inventory is not itself worker or production admission.
        execution_inventory = load("vps-execution-policy.json")["projects"]
        for pid in NEW_PROJECTS:
            self.assertIn(pid, execution_inventory)
            self.assertFalse(self.contracts[pid]["autonomy"]["auto_start"])
            self.assertEqual("external_gate", self.contracts[pid]["autonomy"]["mode"])
            self.assertNotIn(pid, load("portfolio_queue.seed.json") and [
                item["project_id"] for item in load("portfolio_queue.seed.json")
                if item.get("queue_id", "").startswith("autostart-")
            ])


if __name__ == "__main__":
    unittest.main()
