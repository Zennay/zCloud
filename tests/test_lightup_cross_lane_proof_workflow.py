import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "lightup-pr20-pr21-cross-lane-proof.yml"


class LightUpCrossLaneProofWorkflowTests(unittest.TestCase):
    def test_proof_is_read_only_against_zcloud_main(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("repos/Zennay/zCloud/contents/", workflow)
        self.assertNotIn("-f branch=main", workflow)
        self.assertNotIn("--method PUT", workflow)

    def test_pr_heads_are_resolved_at_runtime(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('repos/Zennay/Lightup/pulls/$pr', workflow)
        self.assertIn('--jq .head.sha', workflow)
        self.assertIn('--jq .state', workflow)
        self.assertIn('echo "PR${pr}_SHA=$sha" >> "$GITHUB_ENV"', workflow)

    def test_green_receipt_is_artifact_backed(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("artifacts/lightup-pr20-pr21-cross-lane-proof.json", workflow)
        self.assertIn("PR20_SHA", workflow)
        self.assertIn("PR21_SHA", workflow)


if __name__ == "__main__":
    unittest.main()
