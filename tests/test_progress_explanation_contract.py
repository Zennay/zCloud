import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects.json"
APP = ROOT / "public" / "app.js"


class ProgressExplanationContractTests(unittest.TestCase):
    def test_every_active_project_declares_progress_basis(self):
        projects = json.loads(PROJECTS.read_text(encoding="utf-8"))
        active = [project for project in projects if project.get("status") == "active"]
        self.assertTrue(active)
        for project in active:
            basis = str(project.get("progress_basis") or "").strip()
            self.assertGreaterEqual(
                len(basis),
                20,
                f"{project.get('id')} needs a substantive progress_basis",
            )

    def test_project_detail_renders_project_specific_progress_basis(self):
        app = APP.read_text(encoding="utf-8")
        start = app.index("function detail(p)")
        end = app.index("function workerDetailPanel", start)
        block = app[start:end]
        self.assertIn("${p.progress_basis}", block)
        self.assertIn("This is plan progress.", block)
        self.assertIn("Live results and model quality are not evaluated by this metric.", block)

    def test_card_labels_percentage_as_project_steps_not_quality(self):
        app = APP.read_text(encoding="utf-8")
        start = app.index("function projectCard(p)")
        end = app.index("function attentionPanel", start)
        block = app[start:end]
        self.assertIn("project steps", block)
        self.assertIn("percent of project steps", block)
        self.assertNotIn("success rate", block.lower())
        self.assertNotIn("quality score", block.lower())

    def test_progress_basis_explicitly_separates_quality_where_relevant(self):
        projects = json.loads(PROJECTS.read_text(encoding="utf-8"))
        by_id = {project["id"]: project for project in projects}
        for project_id in ("ftmo", "haxlab", "raiseai"):
            basis = str(by_id[project_id]["progress_basis"]).lower()
            self.assertTrue(
                any(token in basis for token in ("quality", "physical", "code progress")),
                f"{project_id} basis must explain what its build percentage does not prove",
            )

    def test_no_active_project_uses_time_as_progress_basis(self):
        projects = json.loads(PROJECTS.read_text(encoding="utf-8"))
        forbidden = ("time elapsed", "hours worked", "days worked", "estimated time")
        for project in projects:
            if project.get("status") != "active":
                continue
            basis = str(project.get("progress_basis") or "").lower()
            for phrase in forbidden:
                self.assertNotIn(phrase, basis, project.get("id"))


if __name__ == "__main__":
    unittest.main()
