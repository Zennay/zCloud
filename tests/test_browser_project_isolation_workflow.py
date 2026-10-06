import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-browser-project-isolation-contract.yml"


class BrowserProjectIsolationWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_and_permanent_vps_bound(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("persist-credentials: false", text)
        self.assertRegex(text, r"actions/checkout@[0-9a-f]{40}")
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)

    def test_workflow_tracks_real_browser_implementation_surfaces(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for path in (
            "public/zcloud-worker.user.js",
            "firefox-extension/background.js",
            "scripts/zcloud_browser_project_isolation_contract.py",
            "tests/test_browser_project_isolation_contract.py",
            "tests/test_browser_project_isolation_workflow.py",
        ):
            self.assertIn(f"- {path}", text)

    def test_workflow_is_read_only_against_live_runtime(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        forbidden = (
            "systemctl restart",
            "systemctl stop",
            "systemctl start",
            "sqlite3 /home/ubuntu/zennay-cloud/history.db",
            "/api/runner-control",
            "/api/runner-workers",
            "zcloud-vps-deploy",
        )
        for token in forbidden:
            self.assertNotIn(token, text)
        self.assertIn("ZCLOUD_BROWSER_PROJECT_ISOLATION_GREEN=1", text)

    def test_checkout_is_immutable_not_floating(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        checkout_lines = [line.strip() for line in text.splitlines() if "actions/checkout@" in line]
        self.assertEqual(1, len(checkout_lines))
        self.assertNotIn("@v", checkout_lines[0])
        self.assertTrue(re.search(r"@[0-9a-f]{40}", checkout_lines[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
