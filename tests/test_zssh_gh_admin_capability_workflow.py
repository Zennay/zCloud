from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-gh-admin-capability-probe.yml"


class ZsshGhAdminCapabilityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_admission_and_exact_ref_are_trusted(self):
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
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertRegex(
            self.text,
            r"uses: actions/checkout@[0-9a-f]{40} # v7\.0\.1",
        )
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"', self.text)

    def test_runner_guard_precedes_persistent_gh_auth_reads(self):
        guard = self.text.index("scripts/zcloud_vps_runner_guard.py --json")
        auth = self.text.index("gh auth status --hostname github.com")
        repo = self.text.index("gh api repos/Zennay/zSSH --jq")
        protection = self.text.index("branches/main/protection")
        self.assertLess(guard, auth)
        self.assertLess(guard, repo)
        self.assertLess(guard, protection)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)

    def test_actions_are_immutable_and_probe_remains_read_only(self):
        self.assertRegex(
            self.text,
            r"uses: actions/upload-artifact@[0-9a-f]{40} # v7\.0\.1",
        )
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn('"mutation_attempted": False', self.text)
        for forbidden in (
            "gh api --method POST",
            "gh api --method PUT",
            "gh api --method PATCH",
            "gh api --method DELETE",
            "gh repo edit",
            "gh api -X",
            "git push",
            "sudo ",
            "systemctl ",
        ):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
