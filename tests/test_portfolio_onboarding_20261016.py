"""Guard the user-requested MixLab, Fuckball, Tuddy and existing TradeEngine registration."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_PROJECTS = {
    "zsentinel": ("https://github.com/Zennay/zCloud", "3fb9e19ac95581f2b686f01ed9e4dee0"),
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
        self.assertEqual(active_ids, set(self.contracts))
        for pid in NEW_PROJECTS:
            with self.subTest(project=pid):
                contract = self.contracts[pid]
                self.assertEqual(contract["ai_worker_cap"], 1)
                self.assertEqual(contract["autonomy"], {"mode": "external_gate", "auto_start": False})
                self.assertEqual(contract["queue_mode"], "execution")
                self.assertEqual(contract["compute"]["pool"], "build")
                self.assertEqual(contract["compute"]["priority"], "high" if pid == "zsentinel" else "background")
                self.assertFalse(contract["compute"]["protected"])
                self.assertEqual(self.resources[pid], {"priority": "high" if pid == "zsentinel" else "background"})
                self.assertEqual(self.units[pid], [])
                self.assertEqual(self.layout["order"].count(pid), 1)
                self.assertNotIn(pid, self.layout["archived"])

    def test_registry_does_not_authorize_vps_execution(self):
        # Runtime access is gated separately; presentation alone must not grant it.
        execution_allowed = load("vps-execution-policy.json")["projects"]
        for pid in NEW_PROJECTS:
            self.assertNotIn(pid, execution_allowed)


if __name__ == "__main__":
    unittest.main()
