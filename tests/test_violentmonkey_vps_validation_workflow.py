import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-violentmonkey-vps-validation.yml"


class ViolentmonkeyVpsValidationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_self_hosted_pr_execution_is_owner_and_same_repo_guarded(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn(
            "if: github.actor == 'Zennay' && github.triggering_actor == 'Zennay' && "
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)

    def test_checkout_is_exact_head_immutable_and_credential_free(self):
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, flags=re.MULTILINE)
        self.assertEqual(
            uses,
            ["actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"],
        )
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", self.text)
        self.assertIn("fetch-depth: 1", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"', self.text)

    def test_permanent_runner_guard_precedes_repo_validation(self):
        exact = self.text.index("Verify exact candidate revision")
        guard = self.text.index("Guard permanent zCloud runner")
        tests = self.text.index("Regress Violentmonkey dispatch ownership")
        self.assertLess(exact, guard)
        self.assertLess(guard, tests)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_workflow_has_read_only_permissions_and_no_live_mutation_commands(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "rsync ",
            "git push",
            "gh pr merge",
        ):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
