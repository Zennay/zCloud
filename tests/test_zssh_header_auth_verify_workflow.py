from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-header-auth-verify.yml"


class ZsshHeaderAuthVerifyWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_admission_is_owner_same_repo_only(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha",
            self.text,
        )

    def test_uses_permanent_vps_runner_and_immutable_exact_checkout(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertNotIn("runs-on: self-hosted\n", self.text)
        self.assertRegex(
            self.text,
            r"uses: actions/checkout@[0-9a-f]{40} # v6",
        )
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"', self.text)

    def test_runner_guard_precedes_live_zssh_reads(self):
        guard = self.text.index("scripts/zcloud_vps_runner_guard.py --json")
        service = self.text.index("systemctl --user is-active --quiet zssh.service")
        env_read = self.text.index("source /home/ubuntu/.config/zssh/gateway.env")
        canary = self.text.index("mcp-claude-canary.mjs")
        self.assertLess(guard, service)
        self.assertLess(guard, env_read)
        self.assertLess(guard, canary)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)

    def test_remains_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        for forbidden in (
            "systemctl --user restart",
            "systemctl --user stop",
            "systemctl --user start",
            "sudo ",
            "sqlite3 ",
            "rm -",
            "git push",
        ):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
