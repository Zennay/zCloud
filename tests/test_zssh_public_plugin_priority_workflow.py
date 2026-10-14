"""Trust-boundary contract for the zSSH public plugin priority workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-public-plugin-priority.yml"

QUEUE_IDS = (
    "zssh-research-openai-plugin-contract",
    "zssh-universal-endpoint-architecture",
    "zssh-connect-website",
    "zssh-scoped-sudo-grants",
    "zssh-production-oauth",
    "zssh-plugin-package-review-materials",
    "zssh-security-redteam-release",
    "zssh-openai-public-plugin-release",
)


class ZsshPublicPluginPriorityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_and_cannot_touch_live_queue(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  apply:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        self.assertNotIn("/api/portfolio-queue", validate)
        for queue_id in QUEUE_IDS:
            with self.subTest(queue_id=queue_id):
                self.assertNotIn(queue_id, validate)

    def test_live_push_scope_does_not_expand_to_test_only_changes(self):
        push = self.text.split("  push:", 1)[1].split("\n  workflow_dispatch:", 1)[0]
        self.assertIn(".github/workflows/zssh-public-plugin-priority.yml", push)
        self.assertNotIn("tests/test_zssh_public_plugin_priority_workflow.py", push)

    def test_live_apply_requires_canonical_main_and_permanent_runner(self):
        apply = self.text.split("\n  apply:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", apply)
        self.assertIn("github.repository == 'Zennay/zCloud'", apply)
        self.assertIn("github.ref == 'refs/heads/main'", apply)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", apply)

    def test_exact_checkout_and_runner_guard_precede_queue_mutation(self):
        pinned = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(2, self.text.count(pinned))
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        apply = self.text.split("\n  apply:", 1)[1]
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        mutation = "Enqueue and verify zSSH ship-ready lanes in live SQLite"
        self.assertIn('test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"', apply)
        self.assertIn('test "$(id -un)" = "ubuntu"', apply)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', apply)
        self.assertIn('test "${RUNNER_NAME:-}" = "zcloud-vps-1"', apply)
        self.assertLess(apply.index(guard), apply.index(mutation))
        self.assertNotIn("ref: main", apply)

    def test_existing_zssh_queue_contract_is_preserved(self):
        for queue_id in QUEUE_IDS:
            with self.subTest(queue_id=queue_id):
                self.assertEqual(1, self.text.count(f'"queue_id": "{queue_id}"'))
        for token in (
            'payload = {"action": "enqueue", **payload}',
            'base + "/api/portfolio-queue"',
            'str(item.get("project_id") or "").lower() != "zssh"',
            'str(item.get("priority") or "").upper() != "P1"',
            '"RESEARCH_FIRST" in expected["completion_criteria"]',
            'if missing or wrong:',
            'print("ZSSH_SHIP_QUEUE_GREEN"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_live_apply_is_non_cancelling_and_pr_isolated(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.number || 'live'",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
