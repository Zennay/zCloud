"""Trust contract for the push/manual-only zSSH Cloudflare capability probe."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-cloudflare-capability-probe.yml"


class ZsshCloudflareCapabilityProbeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_probe_keeps_pull_requests_out_of_secret_bearing_path(self):
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("github.event.pull_request", self.text)
        self.assertIn("push:", self.text)
        self.assertIn("branches: [main]", self.text)
        self.assertIn("workflow_dispatch:", self.text)

    def test_live_probe_is_canonical_main_only(self):
        probe = self.text.split("\n  probe:", 1)[1]
        self.assertIn("github.repository == 'Zennay/zCloud'", probe)
        self.assertIn("github.ref == 'refs/heads/main'", probe)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", probe)

    def test_checkout_is_immutable_exact_and_credential_free(self):
        pinned = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        self.assertEqual(1, self.text.count(pinned))
        self.assertEqual(1, self.text.count("persist-credentials: false"))
        self.assertIn("ref: ${{ env.EXPECTED_ZCLOUD_SHA }}", self.text)
        self.assertNotIn("actions/checkout@v4", self.text)

    def test_runner_guard_precedes_probe_code(self):
        probe = self.text.split("\n  probe:", 1)[1]
        exact = 'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"'
        hostname = 'test "$(hostname)" = "vps-bb300bba"'
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        capability = "python3 scripts/zssh_cloudflare_capability_probe.py"
        for marker in (exact, hostname, 'test "$(id -un)" = "ubuntu"', guard, capability):
            self.assertIn(marker, probe)
        self.assertLess(probe.index(exact), probe.index(guard))
        self.assertLess(probe.index(guard), probe.index(capability))

    def test_artifact_action_is_immutable(self):
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
            self.text,
        )
        self.assertNotIn("actions/upload-artifact@v4", self.text)

    def test_live_runs_are_not_cancelled_mid_probe(self):
        self.assertIn("group: zssh-cloudflare-capability-probe-live", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_probe_remains_read_only(self):
        for forbidden in (
            "--method POST",
            "--method PUT",
            "--method PATCH",
            "--method DELETE",
            "systemctl restart",
            "systemctl --user restart",
            "sudo ",
            "sqlite3 ",
            "git push",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, self.text)

    def test_permissions_remain_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)


if __name__ == "__main__":
    unittest.main()
