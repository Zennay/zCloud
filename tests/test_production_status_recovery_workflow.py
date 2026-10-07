from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductionStatusRecoveryWorkflowTests(unittest.TestCase):
    def test_missing_current_main_production_status_rearms_regression_only(self):
        text = (
            ROOT / ".github/workflows/zcloud-production-status-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('workflows: ["zCloud VPS deploy"]', text)
        self.assertIn('cron: "*/15 * * * *"', text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("actions: write", text)
        self.assertIn("statuses: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)

        self.assertIn('/commits/{sha}/status', text)
        self.assertIn('== "zcloud/vps-production"', text)
        self.assertIn("if production:", text)
        self.assertIn("PRODUCTION_STATUS_RECOVERY=noop", text)

        self.assertIn("zcloud-vps-deploy.yml/runs", text)
        self.assertIn("PRODUCTION_STATUS_RECOVERY=deploy_already_active", text)
        self.assertIn('"pending", "waiting", "requested"', text)

        self.assertIn("zcloud-regression-smoke.yml/runs", text)
        self.assertIn('str(run.get("head_sha") or "") == sha', text)
        self.assertIn('str(run.get("conclusion") or "") == "success"', text)
        self.assertIn('{"push", "workflow_dispatch"}', text)
        self.assertIn("PRODUCTION_STATUS_RECOVERY=regression_already_active", text)

        self.assertIn("zcloud-regression-smoke.yml/dispatches", text)
        self.assertIn('json.dumps({"ref": "main"})', text)
        self.assertIn("PRODUCTION_STATUS_RECOVERY=regression_dispatched", text)

    def test_recovery_never_writes_vps_or_production_status_directly(self):
        text = (
            ROOT / ".github/workflows/zcloud-production-status-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("statuses: write", text)
        self.assertNotIn("/statuses/", text)
        self.assertNotIn('zcloud/vps-production",', text)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("/home/ubuntu/", text)
        self.assertNotIn("zcloud-vps-deploy.yml/dispatches", text)

    def test_recovery_requires_no_active_current_main_deploy_before_dispatch(self):
        text = (
            ROOT / ".github/workflows/zcloud-production-status-recovery.yml"
        ).read_text(encoding="utf-8")
        deploy_check = text.index("deploy_active = any(")
        regression_check = text.index("exact_green = any(")
        dispatch = text.index("zcloud-regression-smoke.yml/dispatches")
        self.assertLess(deploy_check, regression_check)
        self.assertLess(regression_check, dispatch)
        self.assertIn("if deploy_active:", text)

    def test_write_authority_is_bound_to_canonical_trusted_event_provenance(self):
        text = (
            ROOT / ".github/workflows/zcloud-production-status-recovery.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('CANONICAL_REPOSITORY = "Zennay/zCloud"', text)
        self.assertIn('TRUSTED_EVENTS = {"push", "workflow_dispatch"}', text)
        self.assertIn('event_name = os.environ.get("GITHUB_EVENT_NAME", "")', text)
        self.assertIn('event_ref = os.environ.get("GITHUB_REF", "")', text)
        self.assertIn('event_sha = os.environ.get("GITHUB_SHA", "")', text)
        self.assertIn('event_path = Path(os.environ["GITHUB_EVENT_PATH"])', text)
        self.assertIn('repo != CANONICAL_REPOSITORY', text)
        self.assertIn('event_name not in {"schedule", "workflow_run", "workflow_dispatch"}', text)
        self.assertIn('event_ref != "refs/heads/main"', text)
        self.assertIn('if event_name == "workflow_run":', text)
        self.assertIn('trigger_repo_name == CANONICAL_REPOSITORY', text)
        self.assertIn('str(trigger.get("head_branch") or "") == "main"', text)
        self.assertIn('str(trigger.get("conclusion") or "") == "success"', text)
        self.assertIn('str(trigger.get("event") or "") in TRUSTED_EVENTS', text)
        self.assertIn('trigger_sha != sha', text)
        self.assertIn('event_sha != sha', text)
        self.assertIn("PRODUCTION_STATUS_RECOVERY_ADMISSION=accepted", text)

    def test_provenance_gate_runs_before_any_actions_write_dispatch(self):
        text = (
            ROOT / ".github/workflows/zcloud-production-status-recovery.yml"
        ).read_text(encoding="utf-8")

        repo_gate = text.index("if repo != CANONICAL_REPOSITORY:")
        workflow_run_gate = text.index("if event_name == \"workflow_run\":")
        current_main = text.index('main = get("/commits/main")')
        stale_gate = text.index("trigger_sha != sha")
        dispatch = text.index("zcloud-regression-smoke.yml/dispatches")

        self.assertLess(repo_gate, current_main)
        self.assertLess(workflow_run_gate, current_main)
        self.assertLess(current_main, stale_gate)
        self.assertLess(stale_gate, dispatch)
        self.assertIn(
            "PRODUCTION_STATUS_RECOVERY_REJECT reason=workflow_run_provenance",
            text,
        )
        self.assertIn(
            "PRODUCTION_STATUS_RECOVERY_REJECT reason=stale_workflow_run",
            text,
        )
        self.assertIn(
            "PRODUCTION_STATUS_RECOVERY_REJECT reason=stale_event_sha",
            text,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
