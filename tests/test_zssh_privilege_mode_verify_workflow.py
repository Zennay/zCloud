"""Contract tests for zSSH privilege-mode verification."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-privilege-mode-verify.yml"


class ZsshPrivilegeModeVerifyWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  verify:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)

    def test_live_verify_skips_pull_requests(self):
        verify = self.text.split("\n  verify:", 1)[1]
        self.assertIn("if: github.event_name != 'pull_request'", verify)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", verify)

    def test_checkout_is_immutable_exact_and_credential_free(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertGreaterEqual(
            self.text.count('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'),
            2,
        )

    def test_runner_guard_precedes_live_service_reads(self):
        verify = self.text.split("\n  verify:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        live = "systemctl --user is-active --quiet zssh.service"
        self.assertIn('test "$(id -un)" = "ubuntu"', verify)
        self.assertIn(guard, verify)
        self.assertIn(live, verify)
        self.assertLess(verify.index(guard), verify.index(live))

    def test_privilege_check_remains_read_only(self):
        self.assertIn(
            'test "$(systemctl --user show zssh.service -p NoNewPrivileges --value)" = "no"',
            self.text,
        )
        self.assertIn("grep -q '^ZSSH_EXEC_MODE=full$'", self.text)
        forbidden = (
            "systemctl --user restart",
            "systemctl --user stop",
            "systemctl --user start",
            "sudo ",
            "tee ",
            "sed -i",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_push_is_main_only(self):
        self.assertIn("  push:\n    branches: [main]", self.text)


if __name__ == "__main__":
    unittest.main()
