from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-safe-idle-retry.yml"


class SafeIdleRetryWorkflowTests(unittest.TestCase):
    def test_retry_is_narrow_bounded_and_does_not_weaken_safe_idle(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('workflows: ["zCloud VPS deploy"]', text)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", text)
        self.assertIn("ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED", text)
        self.assertIn("transient-active-blockers", text)
        self.assertIn('item.get("generating") is True or item.get("sending") is True', text)
        self.assertIn("zcloud/vps-safe-idle-retry", text)
        self.assertIn("ZCLOUD_SAFE_IDLE_RETRY_ALREADY_USED", text)
        self.assertIn("--timeout-seconds 900", text)
        self.assertIn("--stable-seconds 20", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("actions: write", text)
        self.assertIn("statuses: write", text)
        self.assertIn("zcloud-regression-smoke.yml/dispatches", text)
        self.assertNotIn("zcloud-vps-deploy.yml/dispatches", text)
        self.assertNotIn("--timeout-seconds 480", text)
        self.assertNotIn("runner-control", text)
        self.assertNotIn("action: start", text)

    def test_retry_uses_least_privilege_and_runner_proof_before_live_read(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        top_permissions = text[text.index("permissions:"):text.index("concurrency:")]
        self.assertNotIn("actions: write", top_permissions)
        self.assertNotIn("statuses: write", top_permissions)

        classify = text[text.index("  classify:"):text.index("  wait-and-dispatch:")]
        self.assertIn("actions: read", classify)
        self.assertIn("statuses: write", classify)

        wait = text[text.index("  wait-and-dispatch:"):text.index("  finalize:")]
        self.assertIn("actions: write", wait)
        self.assertNotIn("statuses: write", wait)
        guard = wait.index("Verify exact revision and permanent zCloud VPS runner")
        live_read = wait.index("Wait read-only for transient blockers")
        self.assertLess(guard, live_read)
        self.assertIn('test "$(git rev-parse HEAD)" = "$DEPLOY_SHA"', wait)
        self.assertIn('test "$(id -un)" = "ubuntu"', wait)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", wait)

        finalize = text[text.index("  finalize:"):]
        self.assertIn("statuses: write", finalize)
        self.assertNotIn("actions: write", finalize)

    def test_retry_reconfirms_exact_main_before_dispatch(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('commits/main" --jq .sha', text)
        self.assertIn('if [[ "$current_main" != "$DEPLOY_SHA" ]]', text)
        self.assertIn("ZCLOUD_SAFE_IDLE_RETRY_MAIN_MOVED", text)
        self.assertIn("obsolete-main-moved", text)

    def test_terminal_retry_status_is_hosted(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        finalize = text[text.index("  finalize:"):]
        self.assertIn("if: always()", finalize)
        self.assertIn("runs-on: ubuntu-latest", finalize)
        self.assertIn("Publish bounded retry result", finalize)
        self.assertIn("state=failure", finalize)
        self.assertIn("state=success", finalize)


if __name__ == "__main__":
    unittest.main(verbosity=2)
