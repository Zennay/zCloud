from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ftmo-stale-ci-cleanup.yml"


class FtmoStaleCiCleanupWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_never_runs_on_self_hosted_runner(self):
        text = self.text
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("python3 -m unittest tests.test_ftmo_stale_ci_cleanup_workflow", text)
        validate = text.index("  validate:")
        cleanup = text.index("  cleanup:")
        self.assertLess(validate, cleanup)
        self.assertNotIn("self-hosted", text[validate:cleanup])

    def test_live_cleanup_is_main_only_on_canonical_permanent_vps(self):
        text = self.text
        self.assertIn("github.repository == 'Zennay/zCloud'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertNotIn("runs-on: self-hosted\n", text)

    def test_checkout_is_immutable_and_credentials_are_not_persisted(self):
        text = self.text
        pin = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"
        self.assertEqual(text.count(pin), 2)
        self.assertGreaterEqual(text.count("persist-credentials: false"), 2)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("github.sha", text)
        self.assertNotIn("actions/checkout@v4", text)

    def test_runner_identity_guard_precedes_any_cross_repo_cancellation(self):
        text = self.text
        guard = text.index("- name: Guard canonical permanent VPS before cancellation")
        cancel = text.index("- name: Cancel only superseded FTMO branch runs")
        self.assertLess(guard, cancel)
        guarded = text[guard:cancel]
        self.assertIn("git rev-parse HEAD", guarded)
        self.assertIn("GITHUB_SHA", guarded)
        self.assertIn("id -un", guarded)
        self.assertIn("vps-bb300bba", guarded)
        self.assertIn("RUNNER_NAME", guarded)
        self.assertIn("zcloud-vps-1", guarded)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", guarded)

    def test_existing_fail_closed_main_sha_protection_is_preserved(self):
        text = self.text
        self.assertIn("repos/Zennay/Ftmo/branches/main", text)
        self.assertIn("refusing current-main run=", text)
        self.assertIn("gh run cancel", text)
        self.assertIn("--repo Zennay/Ftmo", text)


if __name__ == "__main__":
    unittest.main()
