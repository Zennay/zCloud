from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SelfHealAssetsTest(unittest.TestCase):
    def test_restart_dropin_is_unbounded_and_delayed(self):
        text = (ROOT / "deploy/90-zcloud-self-heal.conf").read_text()
        self.assertIn("Restart=always", text)
        self.assertIn("RestartSec=5s", text)
        self.assertIn("StartLimitIntervalSec=0", text)

    def test_timer_is_persistent_and_minutely(self):
        text = (ROOT / "deploy/zcloud-self-heal.timer").read_text()
        self.assertIn("OnUnitActiveSec=60s", text)
        self.assertIn("Persistent=true", text)
        self.assertIn("zcloud-self-heal.service", text)

    def test_ftmo_recovery_does_not_restart_an_active_runner(self):
        text = (ROOT / ".github/workflows/ftmo-persistent-runtime-recovery.yml").read_text()
        self.assertIn('! systemctl is-active --quiet "$RUNNER_SERVICE"', text)
        self.assertIn('systemctl start "$RUNNER_SERVICE"', text)
        self.assertNotIn('systemctl restart "$RUNNER_SERVICE"', text)
        self.assertNotIn('systemctl disable --now "$RUNNER_SERVICE"', text)

    def test_probe_has_disable_sentinel_and_bounded_health_checks(self):
        text = (ROOT / "scripts/zcloud-self-heal.sh").read_text()
        self.assertIn(".disable-self-heal", text)
        self.assertIn("flock -n", text)
        self.assertIn("curl -fsS --max-time 8", text)
        self.assertIn('systemctl restart "${SERVICE}"', text)
        self.assertIn("for _ in {1..20}", text)
        self.assertIn(".disable-runtime-heal", text)
        self.assertIn("haxlab-autonomy.timer", text)
        self.assertIn("ftmo-autonomous-marathon.service", text)
        self.assertNotIn("    ftmo-autonomous.timer", text)
        self.assertIn("raise-gateway.service", text)
        self.assertIn("zssh.service", text)
        self.assertIn('systemctl enable "${service}"', text)
        self.assertIn('user_systemctl enable "${service}"', text)
        self.assertIn('runuser -u "${RUNTIME_USER}"', text)
        self.assertIn('RUNTIME_USER="${ZCLOUD_RUNTIME_USER:-ubuntu}"', text)

if __name__ == "__main__":
    unittest.main()
