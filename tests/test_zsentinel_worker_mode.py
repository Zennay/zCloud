"""zSentinel is a worker mode and a material cross-project issue fixer, never a portfolio card."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ZSentinelModeTests(unittest.TestCase):
    def test_exclusive_display_registry_and_queue_role(self):
        portfolio = json.loads((ROOT / "projects.json").read_text())
        modes = json.loads((ROOT / "worker-modes.json").read_text())
        self.assertNotIn("zsentinel", {x["id"] for x in portfolio})
        self.assertEqual([x["id"] for x in modes], ["zsentinel"])
        self.assertIn("Vliegende keep", modes[0]["name"])
        layout = json.loads((ROOT / "project-layout.json").read_text())
        self.assertNotIn("zsentinel", layout["order"])
        self.assertNotIn("zsentinel", layout["archived"])

    def test_actual_worker_prompt_starts_with_notional_contract(self):
        server = (ROOT / "server.py").read_text()
        self.assertIn("if project_id=='zsentinel':", server)
        self.assertIn("Werk verder aan zSentinel", server)
        self.assertIn("Kijk in Notion", server)
        self.assertIn("PRIORITEIT 1: VERWIJDER BLOKKADES", server)
        self.assertIn("PRIORITEIT 2: OPEN ISSUES EN FEATURES", server)
        self.assertLess(server.index("PRIORITEIT 1: VERWIJDER"),server.index("PRIORITEIT 2: OPEN"))
        self.assertIn("stuur vrije workers", server)
        self.assertIn("alternatieve route", server)
        self.assertIn("roadmapfeatures", server)
        self.assertIn("ander veilig werk", server)
        self.assertIn("WORKER_MODES = _load_worker_modes()", server)

    def test_frontend_offers_role_in_dropdown_and_validates_it(self):
        source = (ROOT / "public/app.js").read_text()
        self.assertIn("...(DATA?.worker_modes||[])", source)
        self.assertEqual(source.count("...(DATA?.worker_modes||[])"), 2)
        self.assertEqual(source.count("...(DATA?.archived_projects||[])"), 2)
        self.assertNotIn("(!verified?' disabled':'')", source)
        self.assertIn("project or worker role", source)

    def test_guarded_deploy_repairs_registry_before_worker_role_promotion(self):
        workflow = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text()
        repair_step = workflow.index("scripts/zcloud_runtime_project_schema_repair.py")
        promote_step = workflow.index("--path server.py")
        self.assertLess(repair_step, promote_step)
        self.assertNotIn("--path worker-modes.json", workflow)
        self.assertIn("--preserve-schema-validated-runtime-config", workflow)
        promoter = (ROOT / "scripts/zcloud_transactional_promote.py").read_text()
        self.assertIn('"worker-modes.json",', promoter)
        self.assertIn('worker_modes = candidate / "worker-modes.json"', promoter)
        self.assertIn('"--worker-modes", str(worker_modes)', promoter)
        repair = (ROOT / "scripts/zcloud_runtime_project_schema_repair.py").read_text()
        self.assertIn("worker_role_ids=role_ids", repair)
        self.assertIn("layout_changed", repair)
        self.assertIn('"project.layout"', repair)
        for source_path in ("scripts/zcloud_recovery.py", "scripts/zcloud_prechange_guard.py"):
            with self.subTest(source=source_path):
                self.assertIn('"worker-modes.json",', (ROOT / source_path).read_text())

    def test_scheduler_has_live_worker_contract_without_privileged_bypass(self):
        contracts = json.loads((ROOT / "project-contracts.json").read_text())["projects"]
        sentinel = contracts["zsentinel"]
        self.assertEqual(sentinel["autonomy"], {"mode": "ai_worker", "auto_start": True})
        self.assertEqual(sentinel["ai_worker_cap"], 1)
        self.assertEqual(sentinel["queue_mode"], "execution")
        items = json.loads((ROOT / "portfolio_queue.seed.json").read_text())
        queue = [x for x in items if x["project_id"] == "zsentinel"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["priority"], "P1")
        self.assertTrue(queue[0]["eligible"])
        self.assertTrue(queue[0]["completion_criteria"].startswith("PRIORITY 1: eliminate"))
        self.assertLess(queue[0]["completion_criteria"].index("PRIORITY 1:"),queue[0]["completion_criteria"].index("PRIORITY 2:"))
        self.assertIn("owner claims", queue[0]["completion_criteria"])

if __name__ == "__main__":
    unittest.main()
