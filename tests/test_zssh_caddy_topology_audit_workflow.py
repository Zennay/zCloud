from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-caddy-topology-audit.yml"


class ZsshCaddyTopologyAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_validate_on_hosted_runner_only(self):
        text = self.text
        self.assertIn("pull_request:", text)
        self.assertIn('runs-on: ubuntu-latest', text)
        self.assertIn("if: github.event_name == 'pull_request'", text)
        self.assertIn("python3 -m unittest -v tests.test_zssh_caddy_topology_audit_workflow", text)
        self.assertIn("if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'", text)

    def test_live_audit_checkout_is_exact_and_credential_free(self):
        text = self.text
        audit = text.split("\n  audit:", 1)[1]
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", audit)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", audit)
        self.assertIn('ref: ${{ github.sha }}', audit)
        self.assertIn("persist-credentials: false", audit)
        self.assertIn("clean: true", audit)
        self.assertIn("fetch-depth: 1", audit)

    def test_live_guard_precedes_runtime_reads_and_receipt_write(self):
        text = self.text
        audit = text.split("\n  audit:", 1)[1]
        guard = audit.index("Enforce canonical zCloud VPS runner")
        read = audit.index("Audit live Caddy topology without exposing configuration")
        receipt = audit.index("Record evidence-backed Caddy topology receipt")
        self.assertLess(guard, read)
        self.assertLess(read, receipt)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", audit)
        self.assertIn('test "$(id -un)" = "ubuntu"', audit)

    def test_pr_validation_cannot_cancel_live_audit(self):
        text = self.text
        self.assertIn(
            "group: zssh-caddy-topology-audit-${{ github.event_name == 'pull_request' && github.event.pull_request.number || 'live' }}",
            text,
        )
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)

    def test_audit_preserves_non_mutating_caddy_semantics(self):
        text = self.text
        self.assertIn('"configuration_content_exposed": False', text)
        self.assertIn('"configuration_mutated": False', text)
        self.assertNotIn("systemctl reload caddy", text)
        self.assertNotIn("systemctl restart caddy", text)
        self.assertNotIn("sudo -n install", text)


if __name__ == "__main__":
    unittest.main()
