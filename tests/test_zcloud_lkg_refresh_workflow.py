"""Trust-boundary contract for the verified LKG refresh workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-lkg-refresh.yml"


class ZcloudLkgRefreshWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  refresh:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertIn("tests.test_zcloud_lkg_refresh_workflow", validate)

    def test_live_refresh_requires_canonical_main_and_permanent_runner(self):
        refresh = self.text.split("\n  refresh:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", refresh)
        self.assertIn("github.repository == 'Zennay/zCloud'", refresh)
        self.assertIn("github.ref == 'refs/heads/main'", refresh)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", refresh)

    def test_exact_checkout_and_runner_guard_precede_capture(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        refresh = self.text.split("\n  refresh:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Verify live production and refresh LKG"
        self.assertIn('test "$(id -un)" = "ubuntu"', refresh)
        self.assertIn(guard, refresh)
        self.assertIn(mutation, refresh)
        self.assertLess(refresh.index(guard), refresh.index(mutation))
        self.assertNotIn("ref: main", refresh)

    def test_pr_validation_cannot_touch_live_lkg_state(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  refresh:", 1)[0]
        for token in ("127.0.0.1:8765", ".local/state/zcloud/recovery", "zcloud_recovery.py", "capture --evidence"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_refresh_is_non_cancelling(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.event_name == 'pull_request' && github.ref || 'live'", self.text)

    def test_existing_lkg_safety_contract_is_preserved(self):
        for token in (
            "zcloud_postdeploy_canary.py",
            "live canary is not green; refusing LKG refresh",
            "zcloud_recovery.py",
            "capture --evidence",
            "zcloud_prechange_guard.py",
            "new LKG did not produce a green pre-change guard",
            "ZCLOUD_LKG_REFRESH_GREEN=1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
