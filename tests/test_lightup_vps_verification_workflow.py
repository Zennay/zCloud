from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/lightup-vps-verification.yml"


class LightUpVpsVerificationWorkflowTests(unittest.TestCase):
    def test_workflow_is_bound_to_permanent_runner_and_exact_zcloud_revision(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertNotIn("runs-on: self-hosted\n", text)
        self.assertNotIn("pull_request:", text)

        pinned_checkout = (
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"
        )
        self.assertIn(pinned_checkout, text)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("fetch-depth: 1", text)

        exact = "Verify exact zCloud checkout"
        guard = "Verify permanent zCloud VPS runner"
        proof = "Test isolated LightUp snapshot"
        self.assertIn(exact, text)
        self.assertIn('test "$actual" = "$GITHUB_SHA"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(id -un)" = ubuntu', text)
        self.assertLess(text.index(exact), text.index(guard))
        self.assertLess(text.index(guard), text.index(proof))

    def test_lightup_validation_remains_isolated_and_non_deploying(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("git -C \"${LIGHTUP_REPO}\" archive", text)
        self.assertIn('TMP_ROOT="$(mktemp -d)"', text)
        self.assertIn("python3 -m compileall -q src tests", text)
        self.assertIn("python3 -m unittest discover -s tests -v", text)

        for forbidden in (
            "systemctl restart",
            "systemctl stop",
            "sudo -n install",
            "docker compose up",
            "scripts/zcloud_transactional_promote.py",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
