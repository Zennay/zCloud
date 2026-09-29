from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VpsDeployWorkflowTests(unittest.TestCase):
    def test_deploy_only_follows_green_main_regression(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["zCloud regression smoke"]', text)
        self.assertIn("branches: [main]", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("cancel-in-progress: true", text)

    def test_deploy_uses_transactional_promotions_without_chat_activation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(4, text.count("scripts/zcloud_transactional_promote.py"))
        for path in (
            "server.py",
            "autonomy-policy.json",
            "firefox-extension/background.js",
            "public/app.js",
            "public/index.html",
            "public/style.css",
            "public/enhancements.js",
            "public/enhancements.css",
            "projects.json",
        ):
            self.assertIn(f"--path {path}", text)
        self.assertIn("deploy/chatgpt-firefox.service", text)
        self.assertIn("systemctl --user set-property --runtime chatgpt-firefox.service CPUWeight=100", text)
        for forbidden in ("runner-control", "action: start", "chatgpt.com"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
