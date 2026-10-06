import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-haxlab-telemetry-inventory.yml"


class HaxLabTelemetryInventoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_self_hosted_inventory_is_trust_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)

    def test_checkout_is_exact_and_credentials_are_not_persisted(self):
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)

    def test_inventory_reads_only_shapes_not_values(self):
        self.assertIn("PRAGMA query_only=ON", self.text)
        self.assertIn("mode=ro", self.text)
        self.assertIn("type(value).__name__", self.text)
        self.assertIn("PRAGMA table_info", self.text)
        self.assertIn("total_changes", self.text)
        self.assertNotIn("SELECT *", self.text)
        self.assertNotIn("print(path.read_text", self.text)

    def test_inventory_has_no_production_mutation_commands(self):
        for token in (
            "sudo ",
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl -X POST",
            "git push",
            "unlink(",
            "write_text(",
        ):
            self.assertNotIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
