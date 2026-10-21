import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-control-plane-resource-protection-proof.yml"


class ResourceProtectionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_live_proof_is_owner_same_repo_and_permanent_runner_only(self):
        text = self.text
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository", text
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)

    def test_checkout_is_immutable_exact_head_without_credentials(self):
        text = self.text
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text
        )
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)

    def test_live_step_is_read_only_and_does_not_pretend_current_host_is_protected(self):
        text = self.text
        live = text[text.index("Audit live effective scheduling protection read-only") :]
        self.assertIn(
            "scripts/zcloud_control_plane_resource_protection.py --json", live
        )
        self.assertIn("export XDG_RUNTIME_DIR=/run/user/1000", live)
        self.assertIn(
            "export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", live
        )
        self.assertNotIn("--require-protected", live)
        for forbidden in (
            "sudo ",
            "systemctl set-property",
            "systemctl restart",
            "systemctl stop",
            "systemctl start",
            "rm -",
            "sqlite3 ",
            "curl -X",
        ):
            self.assertNotIn(forbidden, live)

    def test_workflow_has_read_only_repository_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.text)


if __name__ == "__main__":
    unittest.main()
