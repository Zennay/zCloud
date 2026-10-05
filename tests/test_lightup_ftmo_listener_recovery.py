from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/lightup-ftmo-listener-recovery.yml"


class LightUpFTMOListenerRecoveryTests(unittest.TestCase):
    def test_recovery_is_bounded_to_exact_lightup_proof_and_idle_listener(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('LIGHTUP_RUN_ID: "37314597756"', text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service", text)
        self.assertIn("pgrep -u \"$FTMO_RUN_USER\" -f 'Runner\\.Worker'", text)
        self.assertIn("action=preserve-active-worker", text)
        self.assertIn("systemctl restart \"$FTMO_SERVICE\"", text)
        self.assertIn("LIGHTUP_FTMO_PROOF_SCHEDULING", text)
        self.assertIn("1b5e3f7f1537d399d1d9b1041980610926a21267", text)
        self.assertIn("3109ef771fec3e5b96a1b43ff836dfa50080e4c6", text)
        self.assertIn("8996cfb81801ac008ddb68b413c653f4ae1b6266", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("merge_pull_request", text)


if __name__ == "__main__":
    unittest.main()
