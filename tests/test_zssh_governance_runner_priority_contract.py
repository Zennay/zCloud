import re
import unittest
from pathlib import Path

from scripts.zssh_governance_queue_priority_plan import plan_cancellations


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-governance-runner-priority.yml"
PROOF_WORKFLOW = ROOT / ".github" / "workflows" / "zssh-governance-runner-priority-contract.yml"


class ZsshGovernanceRunnerPriorityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.proof_text = PROOF_WORKFLOW.read_text(encoding="utf-8")

    def test_write_permission_is_narrowly_scoped(self):
        text = self.text
        self.assertIn("permissions:\n  actions: write\n  contents: read", text)
        for forbidden in ("issues: write", "pull-requests: write", "contents: write", "packages: write"):
            self.assertNotIn(forbidden, text)

    def test_candidate_inventory_is_bounded_and_queued_only(self):
        text = self.text
        self.assertIn("gh api --method GET repos/Zennay/zCloud/actions/runs", text)
        self.assertIn("-f status=queued", text)
        self.assertIn("-f per_page=100", text)
        self.assertNotIn("-f per_page=1000", text)

    def test_only_three_read_only_audits_are_cancellation_candidates(self):
        match = re.search(r"allow = \{(?P<body>.*?)\n\s*\}", self.text, re.DOTALL)
        self.assertIsNotNone(match)
        names = set(re.findall(r'"([^"]+)"', match.group("body")))
        self.assertEqual(
            names,
            {
                "zSSH production origin readiness (zCloud lane)",
                "zSSH Caddy topology audit (zCloud lane)",
                "zSSH public gateway VPS preflight (zCloud lane)",
            },
        )

    def test_priority_run_must_itself_be_queued(self):
        text = self.text
        self.assertIn(
            'run.get("name") == governance_name and run.get("status") == "queued"',
            text,
        )
        self.assertIn(
            'run.get("name") == cloudflare_probe_name and run.get("status") == "queued"',
            text,
        )

    def test_only_older_queued_allowlisted_runs_are_selected(self):
        text = self.text
        self.assertIn('run.get("status") == "queued"', text)
        self.assertIn('run.get("name") in allow', text)
        self.assertIn('(run.get("created_at") or "") < target_created', text)

    def test_cancel_endpoint_consumes_validated_numeric_ids_only(self):
        text = self.text
        self.assertIn('case "$run_id" in', text)
        self.assertIn("*[!0-9]*)", text)
        self.assertIn(
            'gh api --method POST "repos/Zennay/zCloud/actions/runs/$run_id/cancel"',
            text,
        )
        self.assertNotIn("--method DELETE", text)

    def test_raw_actions_inventory_is_not_uploaded_as_evidence(self):
        text = self.text
        self.assertIn(
            "path: ${{ runner.temp }}/zssh-governance-runner-priority.json",
            text,
        )
        upload_block = text[text.index("- name: Upload non-secret queue-priority evidence") :]
        self.assertNotIn("zssh-queued-runs.json", upload_block)
        self.assertNotIn("zssh-cancel-queued-ids.txt", upload_block)

    def test_proof_workflow_is_owner_same_repo_guarded(self):
        text = self.proof_text
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)

    def test_proof_workflow_is_exact_head_and_read_only(self):
        text = self.proof_text
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn("uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("ref: ${{ env.EXPECTED_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)

    def test_proof_workflow_requires_permanent_vps_before_contract_test(self):
        text = self.proof_text
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        guard = text.index("python3 scripts/zcloud_vps_runner_guard.py --json")
        test = text.index("python3 -m unittest -v tests.test_zssh_governance_runner_priority_contract")
        self.assertLess(guard, test)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)

    def test_planner_prefers_queued_governance_over_newer_cloudflare_probe(self):
        runs = [
            {"id": 10, "name": "zSSH Cloudflare credential metadata probe", "status": "queued", "created_at": "2026-10-06T18:10:00Z"},
            {"id": 11, "name": "zSSH main protection VPS apply", "status": "queued", "created_at": "2026-10-06T18:00:00Z"},
            {"id": 12, "name": "zSSH public gateway VPS preflight (zCloud lane)", "status": "queued", "created_at": "2026-10-06T17:59:00Z"},
        ]
        plan = plan_cancellations(runs)
        self.assertTrue(plan["governance_run_present"])
        self.assertEqual(plan["priority_run_id"], 11)
        self.assertEqual(plan["cancelled_run_ids"], [12])

    def test_planner_falls_back_to_cloudflare_and_only_cancels_older_queued_allowlisted(self):
        runs = [
            {"id": 20, "name": "zSSH Cloudflare credential metadata probe", "status": "queued", "created_at": "2026-10-06T18:00:00Z"},
            {"id": 21, "name": "zSSH Caddy topology audit (zCloud lane)", "status": "queued", "created_at": "2026-10-06T17:00:00Z"},
            {"id": 22, "name": "zSSH public gateway VPS preflight (zCloud lane)", "status": "in_progress", "created_at": "2026-10-06T16:00:00Z"},
            {"id": 23, "name": "unrelated workflow", "status": "queued", "created_at": "2026-10-06T15:00:00Z"},
            {"id": 24, "name": "zSSH production origin readiness (zCloud lane)", "status": "queued", "created_at": "2026-10-06T18:01:00Z"},
        ]
        plan = plan_cancellations(runs)
        self.assertFalse(plan["governance_run_present"])
        self.assertEqual(plan["priority_run_id"], 20)
        self.assertEqual(plan["cancelled_run_ids"], [21])

    def test_planner_no_priority_run_is_noop(self):
        plan = plan_cancellations([
            {"id": 30, "name": "zSSH public gateway VPS preflight (zCloud lane)", "status": "queued", "created_at": "2026-10-06T17:00:00Z"}
        ])
        self.assertFalse(plan["priority_run_present"])
        self.assertEqual(plan["cancelled_run_ids"], [])

    def test_planner_rejects_unbounded_inventory(self):
        runs = [{} for _ in range(101)]
        with self.assertRaises(ValueError):
            plan_cancellations(runs)


if __name__ == "__main__":
    unittest.main()
