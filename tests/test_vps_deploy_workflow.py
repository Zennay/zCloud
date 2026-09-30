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
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("Reject stale workflow-run revisions", text)
        self.assertIn("git ls-remote origin refs/heads/main", text)
        self.assertIn("STALE_DEPLOY_SKIPPED", text)
        self.assertIn("steps.freshness.outputs.deploy == 'true'", text)

    def test_deploy_uses_transactional_promotions_without_chat_activation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(5, text.count("scripts/zcloud_transactional_promote.py"))
        for path in (
            "server.py",
            "scripts/zcloud_recovery.py",
            "autonomy-policy.json",
            "portfolio_queue.seed.json",
            "firefox-extension/background.js",
            "public/app.js",
            "public/index.html",
            "public/style.css",
            "public/enhancements.js",
            "public/enhancements.css",
            "public/zcloud-worker.user.js",
            "projects.json",
        ):
            self.assertIn(f"--path {path}", text)
        self.assertIn("deploy/chatgpt-firefox.service", text)
        self.assertIn("systemctl --user set-property --runtime chatgpt-firefox.service CPUWeight=100", text)
        for forbidden in ("runner-control", "action: start", "chatgpt.com"):
            self.assertNotIn(forbidden, text)


    def test_execution_probe_claims_cloud_task_on_self_hosted_runner(self):
        text = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py acquire", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py verify", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py release", text)
        self.assertIn("if: always()", text)
        self.assertIn("actions/upload-artifact@v4", text)

        coordination = (ROOT / "scripts/zcloud_vps_deploylane_coordination.py").read_text(encoding="utf-8")
        self.assertIn('PROJECT = "cloud"', coordination)
        self.assertIn('WORKER = "cloud::w1"', coordination)
        self.assertIn('CLAIM = "cloud-permanent-vps-first-deploylane"', coordination)
        self.assertIn('"action": "heartbeat"', coordination)
        self.assertIn('"action": "release"', coordination)
        self.assertIn('"vps_profile": "control_plane"', coordination)
        self.assertIn("claim verification failed", coordination)


if __name__ == "__main__":
    unittest.main()
