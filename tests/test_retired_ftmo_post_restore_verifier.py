import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-safe-gate-post-restore-verify.yml"
PROOF = WORKFLOWS / "zcloud-retired-ftmo-post-restore-verifier-proof.yml"

STALE_RUNTIME_SHA = "8dd37b5c5007c4092d27cb90830a7f9891b4c424"
STALE_DEPLOY_RUN_ID = "36973324400"
STALE_MARKER = "FTMO_SAFE_GATE_POST_RESTORE_GREEN"


class RetiredFtmoPostRestoreVerifierTests(unittest.TestCase):
    def test_stale_post_restore_verifier_is_absent(self):
        self.assertFalse(RETIRED.exists())

    def test_stale_target_markers_are_not_live_workflow_authority(self):
        offenders = []
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            if path == PROOF:
                continue
            retired_fingerprint = (
                STALE_RUNTIME_SHA in text
                and STALE_DEPLOY_RUN_ID in text
                and STALE_MARKER in text
            )
            if retired_fingerprint:
                offenders.append(path.name)
        self.assertEqual([], offenders)

    def test_retirement_proof_is_exact_head_read_only(self):
        text = PROOF.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "/opt/ftmo-autonomous",
            "history.db",
            "curl -X",
            "urllib.request",
            "contents: write",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
