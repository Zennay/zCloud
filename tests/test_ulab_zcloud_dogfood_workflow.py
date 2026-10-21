from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ulab-zcloud-dogfood.yml"


class ULabZCloudDogfoodWorkflowTests(unittest.TestCase):
    def test_self_hosted_pr_execution_is_owner_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertNotIn("runs-on: self-hosted\n", text)
        self.assertIn("cancel-in-progress: true", text)

    def test_checkouts_are_immutable_and_exact(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6"
        self.assertEqual(2, text.count(checkout))
        self.assertNotIn("actions/checkout@v4", text)
        self.assertEqual(2, text.count("persist-credentials: false"))
        self.assertIn(
            "EXPECTED_ZCLOUD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', text)

        self.assertIn("Resolve exact uLab main revision", text)
        self.assertIn(
            "git ls-remote https://github.com/Zennay/uLab.git refs/heads/main",
            text,
        )
        self.assertIn("ref: ${{ steps.ulab_ref.outputs.sha }}", text)
        self.assertIn('test "$(git -C .ulab-tool rev-parse HEAD)" = "$EXPECTED_ULAB_SHA"', text)

    def test_runner_guard_precedes_external_repo_execution(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        guard = "Verify exact zCloud checkout and permanent runner"
        resolve = "Resolve exact uLab main revision"
        build = "Build uLab on VPS"
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(id -un)" = ubuntu', text)
        self.assertLess(text.index(guard), text.index(resolve))
        self.assertLess(text.index(resolve), text.index(build))

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
