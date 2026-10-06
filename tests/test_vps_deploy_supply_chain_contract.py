from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-vps-deploy.yml"
UPLOAD_ARTIFACT_V7_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


class VpsDeploySupplyChainContractTests(unittest.TestCase):
    def test_production_deploy_uses_immutable_artifact_action(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            f"actions/upload-artifact@{UPLOAD_ARTIFACT_V7_SHA} # v7.0.1",
            text,
        )
        self.assertNotIn("actions/upload-artifact@v4", text)
        self.assertNotIn("actions/upload-artifact@v7", text)
        self.assertEqual(1, text.count("actions/upload-artifact@"))

    def test_artifact_pin_does_not_broaden_deploy_trigger_or_permissions(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('workflows: ["zCloud regression smoke"]', text)
        self.assertIn("branches: [main]", text)
        self.assertNotIn("pull_request:", text)
        self.assertIn("permissions:\n  actions: read\n  contents: read\n  statuses: write", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)


if __name__ == "__main__":
    unittest.main()
