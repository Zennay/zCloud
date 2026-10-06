import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-governance-runner-priority.yml"


class ZsshGovernanceRunnerPriorityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

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
        match = re.search(r"allow = \\{(?P<body>.*?)\\n\\s*\\}", self.text, re.DOTALL)
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


if __name__ == "__main__":
    unittest.main()
