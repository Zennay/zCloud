import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"
STYLE = ROOT / "public" / "style.css"


def function_block(source: str, name: str, next_name: str) -> str:
    start = source.find(f"function {name}(")
    end = source.find(f"function {next_name}(", start + 1)
    if start < 0 or end <= start:
        raise AssertionError(f"unable to isolate {name}()")
    return source[start:end]


class ProjectCardInformationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP.read_text(encoding="utf-8")
        cls.style = STYLE.read_text(encoding="utf-8")
        cls.card = function_block(cls.app, "projectCard", "attentionPanel")
        cls.ops = function_block(cls.app, "projectCardOps", "runnerControls")

    def test_card_keeps_identity_goal_health_and_progress_in_primary_link(self):
        for marker in (
            "project-card-link",
            "badge(p)",
            "${p.name}",
            "${p.goal}",
            "p.completed",
            "p.milestones.length",
            "p.progress",
            "progress-track",
        ):
            self.assertIn(marker, self.card)
        self.assertRegex(self.card, r"p\.phase\?'Phase: '.+:'Next: '")

    def test_card_exposes_actionable_control_plane_summary_without_opening_detail(self):
        for marker in (
            "projectPrimaryActions(p)",
            "projectCardOps(p)",
            "mobileRunnerControls(p)",
        ):
            self.assertIn(marker, self.card)
        for marker in (
            "<small>Now</small>",
            "<small>Last action</small>",
            "project-card-problem",
            "scalingChip(p.id)",
        ):
            self.assertIn(marker, self.ops)

    def test_card_summary_bounds_current_tasks_and_error_copy(self):
        self.assertIn("tasks.slice(0,2)", self.ops)
        self.assertIn("tasks.length>2?' · +'", self.ops)
        self.assertIn("error.length>140?error.slice(0,137)+'…':error", self.ops)

    def test_card_does_not_embed_advanced_debug_or_system_panels(self):
        for forbidden in (
            "workerDebugPanel(",
            "workerScalingPanel(",
            "runnerPanel(",
            "activityPanel(",
            "graphPanel(",
            "services(",
        ):
            self.assertNotIn(forbidden, self.card)

    def test_desktop_card_grid_is_dense_and_mobile_reflows(self):
        self.assertRegex(
            self.style,
            r"\.project-grid\{[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)",
        )
        self.assertRegex(
            self.style,
            r"\.project-card\{[^}]*padding:21px",
        )
        self.assertIn("@media(max-width:760px)", self.style)
        self.assertRegex(
            self.style,
            r"@media\(max-width:760px\)\{[^}]*\.project-grid\{[^}]*grid-template-columns:1fr",
        )

    def test_card_has_bounded_visual_hierarchy_instead_of_full_detail_panels(self):
        for marker in (
            ".project-card .eyebrow",
            ".project-card p",
            ".progress-row",
            ".project-bottom",
            ".project-card-ops",
        ):
            self.assertIn(marker, self.style)
        self.assertIn("min-height:41px", self.style)

    def test_workflow_is_exact_head_read_only_permanent_vps(self):
        workflow = (
            ROOT / ".github" / "workflows" / "zcloud-project-card-contract.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", workflow)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', workflow)
        self.assertIn('test "$(id -un)" = "ubuntu"', workflow)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "sqlite3 ",
            "curl -X",
            "gh api --method",
        ):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
