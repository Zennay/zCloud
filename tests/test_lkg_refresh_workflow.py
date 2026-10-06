import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-lkg-refresh.yml"


class LkgRefreshWorkflowSafetyTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_lkg_refresh_is_pinned_to_permanent_vps_runner(self):
        text = self.text()
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertNotIn("uses: actions/checkout@v4", text)

    def test_runner_identity_is_proven_before_live_lkg_mutation(self):
        text = self.text()
        guard = text.index("python3 scripts/zcloud_vps_runner_guard.py --json")
        canary = text.index("scripts/zcloud_postdeploy_canary.py")
        capture = text.index("scripts/zcloud_recovery.py")
        self.assertLess(guard, canary)
        self.assertLess(canary, capture)

    def test_lkg_refresh_keeps_fail_closed_recovery_chain(self):
        text = self.text()
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("live canary is not green; refusing LKG refresh", text)
        self.assertIn("capture --evidence", text)
        self.assertIn("scripts/zcloud_prechange_guard.py", text)
        self.assertIn("new LKG did not produce a green pre-change guard", text)
        self.assertNotIn("continue-on-error", text)


if __name__ == "__main__":
    unittest.main()
