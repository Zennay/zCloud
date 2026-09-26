import unittest
import json
from pathlib import Path
from unittest.mock import patch
import enhancements


class EvidenceProgressTests(unittest.TestCase):
    def test_comparison_schema_is_generic_and_sparse(self):
        latest = enhancements._comparison_point(
            "Nieuwste candidate", "Generation 18", note="development-only", source="trial.json"
        )
        view = enhancements._comparison_view(latest=latest)
        self.assertTrue(view["available"])
        self.assertEqual("Generation 18", view["latest"]["value"])
        self.assertFalse(view["latest"]["validated"])
        self.assertIsNone(view["current"])
        self.assertIsNone(view["best"])

    def test_ftmo_exposes_latest_and_validated_current_without_inventing_best(self):
        cand = (
            18,
            "gen18/trial.json",
            {"trial_hash": "candidate-18"},
            {"total_pnl": 0.0023, "cost_1_5x_pnl": 0.0014, "closed_trades": 80, "win_rate": 0.55},
            {},
        )
        release = (
            17,
            {"paper_release_hash": "release-17"},
            {"trial_hash": "validated-17"},
            {"result": {"total_pnl": 0.0018, "cost_1_5x_pnl": 0.0010, "win_rate": 0.52}},
        )
        with patch.object(enhancements, "_ftmo_candidate", return_value=cand),              patch.object(enhancements, "_ftmo_release", return_value=release),              patch.object(enhancements, "_ftmo_readiness", return_value={"available": True}):
            quality = enhancements._ftmo_quality()
        cmp = quality["comparison"]
        self.assertEqual("Generation 18", cmp["latest"]["value"])
        self.assertFalse(cmp["latest"]["validated"])
        self.assertEqual("Generation 17", cmp["current"]["value"])
        self.assertTrue(cmp["current"]["validated"])
        self.assertIsNone(cmp["best"], "Do not invent a best release without a comparable validated criterion")

    def test_all_project_progress_has_documented_checkpoint_basis(self):
        projects = json.loads(Path("projects.json").read_text(encoding="utf-8"))
        self.assertTrue(projects)
        for project in projects:
            with self.subTest(project=project["id"]):
                self.assertTrue(project.get("milestone_revision"), "progress must identify its checkpoint revision")
                self.assertTrue(project.get("progress_basis"), "progress must explain what the percentage means")
                if "progress_override" in project:
                    self.assertTrue(
                        project.get("scorecard_url") or project.get("notion_url"),
                        "an override must point at a documented scorecard/source",
                    )

    def test_unknown_project_has_empty_comparison_contract(self):
        quality = enhancements.quality_for("unknown")
        self.assertFalse(quality["comparison"]["available"])
        self.assertIsNone(quality["comparison"]["latest"])
        self.assertIsNone(quality["comparison"]["current"])
        self.assertIsNone(quality["comparison"]["best"])

    def test_renderer_is_project_agnostic(self):
        js = Path("public/enhancements.js").read_text(encoding="utf-8")
        start = js.index("function evidencePanel")
        end = js.index("function qPanel", start)
        renderer = js[start:end]
        self.assertNotIn("p.id===", renderer)
        self.assertNotIn("ftmo", renderer.lower())
        self.assertNotIn("haxlab", renderer.lower())
        self.assertIn("latest", renderer)
        self.assertIn("current", renderer)
        self.assertIn("best", renderer)
        self.assertIn("Actuele bottleneck", renderer)


if __name__ == "__main__":
    unittest.main(verbosity=2)