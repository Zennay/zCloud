import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkerQualityRetryNonPauseTests(unittest.TestCase):
    def test_short_generations_escalate_prompt_in_both_chatgpt_drivers(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")

        for source in (background, userscript):
            self.assertNotIn("Werk verder aan het project en voer nu een concrete volgende stap uit", source)
            self.assertIn('return String(basePrompt || "");', source)
            self.assertNotIn("ZCLOUD_QUALITY_RETRY #", source)
            self.assertNotIn("ANDERE veilige uitvoeringsroute", source)
            self.assertIn("quality-retry-cleared", source)
            self.assertIn("valid-generation", source)

        self.assertNotIn("autoPauseForHealth", background)
        self.assertIn("recoverWeakCycleHealth", background)
        self.assertIn('QUALITY_RETRY_LIMIT = "unbounded"', background)

    def test_backend_only_persists_explicit_dashboard_pause(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("if event=='runner-auto-paused':", server)
        self.assertIn("backend-rejected-automatic-worker-pause", server)
        self.assertIn("if event=='runner-paused' and reason!='dashboard-pause':", server)
        self.assertIn("backend-rejected-non-dashboard-worker-pause", server)
        self.assertIn("if event in ('runner-drained','runner-paused')", server)


if __name__ == "__main__":
    unittest.main()
