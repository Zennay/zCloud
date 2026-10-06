import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/zcloud-live-debug.yml")


class LiveDebugWorkflowTests(unittest.TestCase):
    def test_pr_self_hosted_execution_is_trusted_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "if: github.event_name != 'pull_request' || (github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository)",
            text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("ref: ${{ env.EXPECTED_SHA }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_live_history_db_read_is_query_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('f"file:{db_path}?mode=ro"', text)
        self.assertIn("uri=True", text)
        self.assertIn('conn.execute("PRAGMA query_only=ON")', text)
        self.assertIn("db_path.is_symlink()", text)
        self.assertNotIn('sqlite3.connect(root/"history.db",timeout=4)', text)
        self.assertNotIn("INSERT INTO config_audit", text)
        self.assertNotIn("UPDATE config_audit", text)
        self.assertNotIn("DELETE FROM config_audit", text)

    def test_diagnostic_semantics_remain_observational(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Read live canary and worker state", text)
        self.assertIn("LIVE PRECHANGE GUARD", text)
        self.assertIn("LIVE POSTDEPLOY CANARY", text)
        self.assertIn("DEPLOY DRIFT PROVENANCE", text)
        self.assertIn("AUDITED RESOURCE DRIFT DECISION", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("systemctl stop", text)
        self.assertNotIn("systemctl start", text)
        self.assertNotIn("rm -rf", text)


if __name__ == "__main__":
    unittest.main()
