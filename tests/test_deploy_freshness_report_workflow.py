from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-deploy-freshness-proof.yml"


class DeployFreshnessWorkflowTests(unittest.TestCase):
    def test_proof_is_exact_head_and_permanent_vps_bound(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$actual" = "$EXPECTED_ZCLOUD_SHA"', text)

    def test_runner_guard_precedes_contract_execution(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        guard = text.index("python3 scripts/zcloud_vps_runner_guard.py --json")
        tests = text.index("python3 -m unittest -v")
        self.assertLess(guard, tests)
        self.assertIn("ZCLOUD_DEPLOY_FRESHNESS_PROOF_GREEN=1", text)

    def test_proof_does_not_touch_live_runtime_state(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        for forbidden in (
            "/home/ubuntu/zennay-cloud",
            "history.db",
            "systemctl",
            "scripts/zcloud_transactional_promote.py",
            "scripts/zcloud_runtime.py",
            "curl ",
            "sudo ",
        ):
            self.assertNotIn(forbidden, text)

    def test_relevant_contract_surfaces_trigger_the_proof(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        for path in (
            "scripts/zcloud_deploy_freshness_report.py",
            "tests/test_deploy_freshness_report.py",
            "tests/test_deploy_freshness_report_workflow.py",
            "scripts/zcloud_recovery.py",
            ".github/workflows/zcloud-vps-deploy.yml",
            ".github/workflows/zcloud-regression-smoke.yml",
        ):
            self.assertGreaterEqual(
                text.count(f"      - {path}"),
                2,
                f"{path} must trigger pull_request and push proof",
            )


if __name__ == "__main__":
    unittest.main()
