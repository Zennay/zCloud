from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SelfHealAssetsTest(unittest.TestCase):
    def test_self_heal_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts/zcloud-self-heal.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_restart_dropin_is_unbounded_and_delayed(self):
        text = (ROOT / "deploy/90-zcloud-self-heal.conf").read_text()
        self.assertIn("Restart=always", text)
        self.assertIn("RestartSec=5s", text)
        self.assertIn("StartLimitIntervalSec=0", text)

    def test_timer_is_persistent_and_checks_every_30_seconds(self):
        text = (ROOT / "deploy/zcloud-self-heal.timer").read_text()
        self.assertIn("OnUnitActiveSec=30s", text)
        self.assertIn("Persistent=true", text)
        self.assertIn("zcloud-self-heal.service", text)

    def test_ftmo_recovery_does_not_restart_an_active_runner(self):
        text = (ROOT / ".github/workflows/ftmo-persistent-runtime-recovery.yml").read_text()
        self.assertIn('! systemctl is-active --quiet "$RUNNER_SERVICE"', text)
        self.assertIn('systemctl start "$RUNNER_SERVICE"', text)
        self.assertNotIn('systemctl disable --now "$RUNNER_SERVICE"', text)
        guard = "elif pgrep -u ftmo-runner -f 'Runner\\.Worker' >/dev/null; then"
        restart = 'sudo -n systemctl restart "$RUNNER_SERVICE"'
        self.assertIn(guard, text)
        self.assertIn("sleep 5", text)
        self.assertIn("FTMO Actions Runner.Worker appeared during guard window", text)
        self.assertIn(restart, text)
        self.assertGreaterEqual(text.count("pgrep -u ftmo-runner -f 'Runner\\.Worker'"), 2)
        self.assertLess(text.index(guard), text.index(restart))
        self.assertLess(text.index("sleep 5"), text.index(restart))

    def test_probe_recovers_only_inactive_zcloud_actions_runner(self):
        text = (ROOT / "scripts/zcloud-self-heal.sh").read_text()
        self.assertIn('ZCLOUD_ACTIONS_RUNNER_NAME:-zcloud-vps-1', text)
        self.assertIn('ZCLOUD_ACTIONS_RUNNER_ROOT:-/home/ubuntu/actions-runner-zcloud', text)
        self.assertIn("systemctl list-unit-files --type=service --no-legend 'actions.runner.*'", text)
        self.assertIn('runtime_disabled "${unit}"', text)
        self.assertIn('systemctl is-active --quiet "${unit}"', text)
        self.assertIn("Runner\\\\.Worker", text)
        self.assertIn("sleep 3", text)
        self.assertIn("Actions Runner.Worker appeared during guard window", text)
        self.assertIn('systemctl start "${unit}"', text)
        self.assertNotIn('systemctl restart "${unit}"', text)
        self.assertGreaterEqual(text.count("Runner\\\\.Worker"), 2)
        self.assertLess(text.index('systemctl is-active --quiet "${unit}"'), text.index('systemctl start "${unit}"'))
        self.assertLess(text.index("sleep 3"), text.index('systemctl start "${unit}"'))

    def test_probe_has_disable_sentinel_and_bounded_health_checks(self):
        text = (ROOT / "scripts/zcloud-self-heal.sh").read_text()
        self.assertIn(".disable-self-heal", text)
        self.assertIn("flock -n", text)
        self.assertIn("curl -fsS --max-time 8", text)
        self.assertIn('URL="${ZCLOUD_HEALTH_URL:-http://127.0.0.1:8765/}"', text)
        self.assertNotIn("http://127.0.0.1:8765/api/status", text)
        self.assertIn('systemctl restart "${SERVICE}"', text)
        self.assertIn('systemctl is-active --quiet "${SERVICE}"', text)
        self.assertIn('logger -t zcloud-self-heal "health probe timed out; confirming before restart"', text)
        self.assertIn("sleep 3", text)
        self.assertIn('logger -t zcloud-self-heal "health recovered during confirmation window"', text)
        self.assertIn('logger -t zcloud-self-heal "health failed twice; restarting ${SERVICE}"', text)
        self.assertLess(
            text.index('health probe timed out; confirming before restart'),
            text.index('systemctl restart "${SERVICE}"'),
        )
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
        self.assertIn("legacy_violentmonkey_only()", text)
        self.assertIn("ExecCondition=/bin/false", text)
        self.assertIn('if [[ "${service}" == "chatgpt-firefox.service" ]] && legacy_violentmonkey_only; then', text)
        self.assertIn("heal_worker_progress", text)
        self.assertLess(
            text.index('if [[ "${service}" == "chatgpt-firefox.service" ]] && legacy_violentmonkey_only; then'),
            text.index("heal_worker_progress()"),
        )

if __name__ == "__main__":
    unittest.main()
