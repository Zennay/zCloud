from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VpsDeployWorkflowTests(unittest.TestCase):
    def test_green_main_handoff_never_waits_for_repo_specific_vps_runner(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["zCloud regression smoke"]', text)
        self.assertIn("branches: [main]", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertNotIn("runs-on: self-hosted", text)

    def test_workflow_only_hands_off_and_does_not_mutate_vps(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("zcloud-autodeploy.timer", text)
        for forbidden in (
            "scripts/zcloud_transactional_promote.py",
            "runner-control",
            "action: start",
            "chatgpt.com",
            "sudo ",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
