import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/zcloud-violentmonkey-update-path.yml")


class ViolentmonkeyUpdatePathWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_never_runs_live_inspection(self):
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("inspect:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)

    def test_live_diagnostic_is_exact_head_and_permanent_runner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertNotIn('runs-on: self-hosted', self.text)

    def test_installed_extension_source_snippets_are_not_logged(self):
        self.assertNotIn('"snippets"', self.text)
        self.assertNotIn('"snippet"', self.text)
        self.assertIn('"size_bytes"', self.text)
        self.assertIn('"matches": sorted(set(matched))', self.text)
        self.assertIn('"anchors":sorted(set(anchors))', self.text)

    def test_diagnostic_remains_non_mutating(self):
        self.assertNotIn("systemctl restart", self.text)
        self.assertNotIn("systemctl stop", self.text)
        self.assertNotIn("systemctl start", self.text)
        self.assertNotIn("sudo ", self.text)


if __name__ == "__main__":
    unittest.main()
