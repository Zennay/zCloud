import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-haxlab-telemetry-report.yml"
REPORT = ROOT / "scripts" / "zcloud_haxlab_telemetry_report.py"


class HaxLabTelemetryReportWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.report = REPORT.read_text(encoding="utf-8")

    def test_exact_head_and_read_only_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.workflow,
        )
        self.assertGreaterEqual(self.workflow.count("persist-credentials: false"), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.workflow)

    def test_permanent_vps_proof_is_same_repo_owner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.workflow)
        self.assertIn("github.actor == 'Zennay'", self.workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.workflow)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.workflow)

    def test_live_proof_requires_all_core_projections_and_source_immutability(self):
        self.assertIn("--json --require-core", self.workflow)
        self.assertIn('test "$before" = "$after"', self.workflow)
        self.assertIn("ZCLOUD_HAXLAB_TELEMETRY_VPS_GREEN=1", self.workflow)
        for key in ("autonomy", "candidate", "champion", "evaluation", "training", "replay"):
            self.assertIn(f'payload["{key}"]["available"] is True', self.workflow)

    def test_workflow_has_no_production_mutation_commands(self):
        for token in (
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl -X POST",
            "git push",
            "rm -",
            "mv ",
            "cp ",
            "write_text(",
        ):
            self.assertNotIn(token, self.workflow)

    def test_privileged_reader_is_fixed_path_and_read_only(self):
        self.assertIn('["sudo", "-n", "cat", "--", str(path)]', self.report)
        self.assertIn("PRIVILEGED_JSON", self.report)
        self.assertNotIn('parser.add_argument("--current', self.report)
        self.assertNotIn('parser.add_argument("--live', self.report)
        self.assertIn("mode=ro", self.report)
        self.assertIn("PRAGMA query_only=ON", self.report)


if __name__ == "__main__":
    unittest.main()
