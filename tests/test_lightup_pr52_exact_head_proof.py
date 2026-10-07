from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/lightup-pr52-exact-head-proof.yml"


class LightUpPr52ExactHeadProofTrustTests(unittest.TestCase):
    def test_active_proof_uses_exact_immutable_zcloud_checkout(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertNotIn("actions/checkout@v4", text)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("clean: true", text)
        self.assertIn("fetch-depth: 1", text)

    def test_active_proof_keeps_read_only_permanent_runner_boundary(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertNotIn("\n  pull_request:", text)
        self.assertNotIn("git push", text)

    def test_active_proof_stays_bound_to_open_lightup_pr52_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "LIGHTUP_EXPECTED_SHA: e44abdc141ea21e3298894238bd14c62e0c1227f",
            text,
        )
        self.assertIn("refs/pull/52/head:refs/remotes/origin/lightup-pr52-proof", text)
        self.assertIn('test "${ACTUAL_SHA}" = "${LIGHTUP_EXPECTED_SHA}"', text)


if __name__ == "__main__":
    unittest.main()
