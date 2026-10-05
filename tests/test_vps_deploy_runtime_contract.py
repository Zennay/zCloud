from pathlib import Path
import unittest


class VpsDeployRuntimeContractTests(unittest.TestCase):
    def test_guarded_deploy_promotes_live_healthcheck(self):
        workflow = Path(".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        start = workflow.index("- name: Promote autonomy policies and queue seed")
        end = workflow.index("- name: Promote Violentmonkey worker", start)
        promotion = workflow[start:end]
        self.assertIn("--path scripts/zcloud_healthcheck.py", promotion)


if __name__ == "__main__":
    unittest.main()
