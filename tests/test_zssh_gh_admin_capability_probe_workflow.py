"""Trust contract for the zSSH GitHub admin capability probe."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-gh-admin-capability-probe.yml"


class ZsshGhAdminCapabilityProbeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  probe:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)

    def test_live_probe_is_canonical_main_only(self):
        probe = self.text.split("\n  probe:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", probe)
        self.assertIn("github.repository == 'Zennay/zCloud'", probe)
        self.assertIn("github.ref == 'refs/heads/main'", probe)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", probe)

    def test_checkout_and_artifact_actions_are_immutable(self):
        checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        upload = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
        self.assertEqual(2, self.text.count(checkout))
        self.assertIn(upload, self.text)
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertNotIn("actions/checkout@v4", self.text)
        self.assertNotIn("actions/upload-artifact@v4", self.text)

    def test_exact_sha_and_runner_guard_precede_gh_reads(self):
        probe = self.text.split("\n  probe:", 1)[1]
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        gh_read = "gh api repos/Zennay/zSSH --jq"
        self.assertIn(exact, probe)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', probe)
        self.assertIn('test "$(id -un)" = "ubuntu"', probe)
        self.assertIn(guard, probe)
        self.assertIn(gh_read, probe)
        self.assertLess(probe.index(exact), probe.index(guard))
        self.assertLess(probe.index(guard), probe.index(gh_read))

    def test_live_concurrency_does_not_cancel_live_probe(self):
        self.assertIn(
            "group: zssh-gh-admin-capability-probe-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || 'live' }}",
            self.text,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.text,
        )

    def test_probe_remains_read_only(self):
        self.assertIn('"mutation_attempted": False', self.text)
        self.assertIn("gh api repos/Zennay/zSSH/branches/main/protection", self.text)
        forbidden = (
            "--method POST",
            "--method PUT",
            "--method PATCH",
            "--method DELETE",
            "-X POST",
            "-X PUT",
            "-X PATCH",
            "-X DELETE",
            "gh api --method",
            "git push",
            "systemctl restart",
            "sudo ",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)

    def test_permissions_remain_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)


if __name__ == "__main__":
    unittest.main()
