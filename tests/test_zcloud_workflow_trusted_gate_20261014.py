"""Trusted-base pull_request_target source/admission regression checks."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-trusted-workflow-change-admission-20261014.yml"
SCANNER = ROOT / "scripts/zcloud_workflow_change_gate_20261014.py"
spec = importlib.util.spec_from_file_location("workflow_gate", SCANNER)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class TrustedBaseWorkflowAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_trusted_event_only_and_no_live_main_push(self):
        self.assertIn("  pull_request_target:", self.source)
        self.assertNotIn("\n  push:", self.source)
        self.assertNotIn("\n  workflow_dispatch:", self.source)
        self.assertEqual(gate.judge(self.source), [])

    def test_runs_only_git_hosted_readonly(self):
        self.assertIn("    runs-on: ubuntu-latest", self.source)
        self.assertNotIn("self-hosted", self.source)
        self.assertIn("permissions:\n  contents: read\n", self.source)
        self.assertNotIn("contents: write", self.source)
        self.assertNotIn("actions: write", self.source)

    def test_both_checkouts_immutable_credential_free(self):
        checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        self.assertEqual(self.source.count(checkout), 2)
        self.assertEqual(self.source.count("persist-credentials: false"), 2)
        self.assertIn("ref: " + "$" + "{{ github.event.pull_request.base.sha }}", self.source)
        self.assertIn("ref: " + "$" + "{{ github.event.pull_request.head.sha }}", self.source)

    def test_candidate_is_never_executed(self):
        self.assertIn("path: candidate", self.source)
        self.assertIn("path: trusted", self.source)
        self.assertIn("cd trusted\n", self.source)
        self.assertNotIn("cd candidate\n", self.source)
        self.assertNotIn("python3 candidate/", self.source)
        self.assertIn("python3 scripts/zcloud_workflow_change_gate_20261014.py --root", self.source)

    def test_latest_main_provenance_and_checkout_enforced(self):
        for marker in (
            'test "$(git -C trusted rev-parse HEAD)" = "$EXACT_BASE"',
            'test "$(git -C candidate rev-parse HEAD)" = "$EXACT_HEAD"',
            "git ls-remote --exit-code https://github.com/Zennay/zCloud.git refs/heads/main",
            'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
        ):
            self.assertIn(marker, self.source)

    def test_no_privileged_mutation_commands(self):
        for marker in ("sudo -n ", "systemctl restart", "systemctl stop",
                       "gh run cancel", "curl -X POST", "GH_TOKEN", "secrets."):
            self.assertNotIn(marker, self.source)

    def test_trusted_root_override_sent_to_subprocess(self):
        root = Path("/tmp/unique-candidate-root")
        with mock.patch.object(gate.subprocess, "check_output", return_value=b"") as run:
            self.assertEqual(gate.changed_workflows("a" * 40, "b" * 40, root), [])
            self.assertEqual(run.call_args.kwargs["cwd"], root)

    def test_symlink_ancestor_is_denied_before_read(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "elsewhere"
            outside.mkdir()
            (root / ".github").symlink_to(outside, target_is_directory=True)
            result = gate.evaluate([".github/workflows/evil.yml"], root)
            self.assertEqual(result, [".github/workflows/evil.yml:symlink_ancestor"])

    def test_second_level_symlink_is_denied(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".github").mkdir()
            (root / "elsewhere").mkdir()
            (root / ".github/workflows").symlink_to(root / "elsewhere", target_is_directory=True)
            self.assertEqual(
                gate.evaluate([".github/workflows/evil.yml"], root),
                [".github/workflows/evil.yml:symlink_ancestor"],
            )


if __name__ == "__main__":
    unittest.main()
