from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SimpleLanguageTests(unittest.TestCase):
    def test_main_ui_uses_plain_english_labels(self):
        app = (ROOT / "public" / "app.js").read_text()
        for old in (
            "Runner live",
            "Runner offline",
            "<small>Heartbeat</small>",
            "Elke tab krijgt een eigen work-lane.",
            "Commits & milestone-updates",
            "Milestones · Amsterdamse tijd",
            "<h2>Milestones</h2>",
            "Milestones afgerond",
        ):
            self.assertNotIn(old, app)
        for expected in (
            "ChatGPT automation",
            "Last active",
            "own workspace",
            "Code and project updates",
            "Project steps · Amsterdam time",
            "<h2>Project steps</h2>",
            "Project steps completed",
        ):
            self.assertIn(expected, app)

    def test_project_cards_show_plain_operational_truth(self):
        app = (ROOT / "public" / "app.js").read_text()
        css = (ROOT / "public" / "enhancements.css").read_text()
        for expected in (
            "function projectCardOps(p)",
            "<small>Now</small>",
            "<small>Last action</small>",
            "<small>Problem</small>",
            "current_task",
            "last_generation_finished",
            "last_prompt_sent",
            "projectCardOps(p)",
            "no task claimed yet",
        ):
            self.assertIn(expected, app)
        self.assertIn(".project-card-ops", css)
        self.assertIn(".project-card-problem", css)
        for forbidden in (
            "<small>Heartbeat</small>",
            "<small>Tab ID</small>",
            "<small>Branch</small>",
        ):
            self.assertNotIn(forbidden, app)

    def test_ftmo_raw_metrics_are_progressively_disclosed(self):
        js = (ROOT / "public" / "enhancements.js").read_text()
        for old in (
            "FTMO readiness",
            "Challenge-path testing",
            "Risk / trade",
            "wacht op chronologische R-path test",
            "(r.measured?'GEMETEN':'PENDING')",
            "<h2>Milestone progress</h2>",
        ):
            self.assertNotIn(old, js)
        self.assertIn("<h2>FTMO test status</h2>", js)
        self.assertIn("Realistic FTMO simulation test", js)
        self.assertIn("Risk per trade", js)
        self.assertIn("Not measured yet", js)
        self.assertIn('details class="section-details readiness-details"', js)
        self.assertIn("Technical test details", js)

    def test_backend_user_facing_copy_avoids_raw_heartbeat_and_rpath_terms(self):
        py = (ROOT / "enhancements.py").read_text()
        self.assertNotIn("Worker heartbeat loopt achter", py)
        self.assertNotIn("kandidaat-specifieke chronologische R-paths", py)
        self.assertIn("Worker is te lang niet actief geweest", py)
        self.assertIn("realistische FTMO-simulatietests", py)

if __name__ == "__main__":
    unittest.main()
