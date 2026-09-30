import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MobileRunnerControlsTests(unittest.TestCase):
    def test_mobile_project_controls_are_rendered(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function mobileRunnerControls", app)
        self.assertIn('data-runner-toggle="', app)
        self.assertIn('data-runner-workers="', app)
        self.assertIn("${mobileRunnerControls(p)}${runnerControls(p)}", app)
        self.assertIn("${mobileRunnerControls(p,'detail')}${workerDetailPanel(p)}", app)


    def test_global_dynamic_worker_control_is_mobile_visible(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "public" / "enhancements.css").read_text(encoding="utf-8")
        self.assertIn("function dynamicWorkerControl", app)
        self.assertIn("chatgpt_count", app)
        self.assertIn("claude_count", app)
        self.assertIn("chatgpt_cooldown_seconds", app)
        self.assertIn("claude_cooldown_seconds", app)
        self.assertIn("check_interval_ms", app)
        self.assertIn("saveDynamicWorkerSettings", app)
        self.assertIn("data-save-dynamic-workers", app)
        self.assertIn(".dynamic-worker-control", css)
        self.assertIn(".dynamic-worker-provider-grid", css)
        self.assertIn("@media(max-width:760px)", css)

    def test_project_card_tap_opens_detail_without_pointer_heuristic(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const projectCardTarget=e.target.closest('[data-project-id]')", app)
        self.assertIn("location.hash='project/'+encodeURIComponent(projectCardTarget.dataset.projectId)", app)
        self.assertNotIn("const mobileCard=e.target.closest('[data-project-id]');if(mobileCard&&window.matchMedia", app)

    def test_mobile_overview_is_always_reachable(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('class="mobile-overview-link', app)
        self.assertIn('data-nav="overview"', app)
        self.assertIn("const navAction=e.target.closest('[data-nav]')", app)
        self.assertIn('class="back" href="#overview" data-nav="overview"', app)

    def test_mobile_controls_have_visible_responsive_styles(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".mobile-runner-controls{display:none}", css)
        self.assertIn("@media(max-width:760px)", css)
        self.assertIn(".mobile-runner-controls{display:grid", css)
        self.assertIn(".mobile-runner-button", css)
        self.assertIn(".mobile-runner-workers input", css)


if __name__ == "__main__":
    unittest.main()
