import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-worker-start-blockers-proof.yml"


class WorkerStartBlockerWorkflowTests(unittest.TestCase):
    def test_workflow_uses_exact_permanent_runner_and_read_only_status(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('fetch_status("http://127.0.0.1:8765/api/status"', text)
        self.assertIn("db.stat().st_mtime_ns", text)
        self.assertIn("ZCLOUD_WORKER_START_BLOCKERS_READONLY_GREEN=1", text)

        for forbidden in (
            "/api/runner-control",
            "/api/config",
            "/api/worker-preflight",
            "INSERT INTO ",
            "UPDATE ",
            "DELETE FROM ",
            "sudo ",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
