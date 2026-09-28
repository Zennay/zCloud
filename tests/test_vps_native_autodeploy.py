from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VpsNativeAutodeployTests(unittest.TestCase):
    def test_autodeployer_requires_exact_green_main_and_holds_new_ai_cycles(self):
        text = (ROOT / "scripts/zcloud_autodeploy.py").read_text(encoding="utf-8")
        self.assertIn('WORKFLOW_NAME = "zCloud regression smoke"', text)
        self.assertIn('"head_sha": sha', text)
        self.assertIn('item.get("head_sha") == sha', text)
        self.assertIn("deployment_hold(target)", text)
        self.assertIn("wait_for_idle()", text)
        self.assertIn("GLOBAL", (ROOT / "server.py").read_text(encoding="utf-8"))
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_HOLD_FILE", server)
        self.assertIn("deployment_hold_active()", server)
        self.assertIn("'deployment_hold':True", server)

    def test_autodeploy_uses_transactional_promotions_and_no_chat_trigger(self):
        text = (ROOT / "scripts/zcloud_autodeploy.py").read_text(encoding="utf-8")
        self.assertIn("zcloud_transactional_promote.py", text)
        self.assertIn("zcloud_postdeploy_canary.py", text)
        self.assertIn("zcloud_prechange_guard.py", text)
        self.assertIn("zcloud_config_validate.py", text)
        self.assertNotIn("chatgpt.com", text)
        self.assertNotIn("runner-control", text)

    def test_timer_runs_vps_local_installed_agent(self):
        service = (ROOT / "deploy/zcloud-autodeploy.service").read_text(encoding="utf-8")
        timer = (ROOT / "deploy/zcloud-autodeploy.timer").read_text(encoding="utf-8")
        self.assertIn("/home/ubuntu/.local/bin/zcloud-autodeploy.py", service)
        self.assertIn("OnUnitActiveSec=2min", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
