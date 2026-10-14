"""Offline Git cancellation policy regression suite. Does not touch VPS."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/zcloud_deployops_cancellation_ratchet_20261014.py"
spec = importlib.util.spec_from_file_location("zcloud_cancellation", MODULE)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)

SAFE = """name: safe
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: isolated
  cancel-in-progress: false
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: echo safe
"""
SELFHOST = SAFE.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted, zcloud, vps]")
CANCEL = SELFHOST.replace("cancel-in-progress: false", "cancel-in-progress: true")
DYNAMIC = CANCEL.replace("runs-on: [self-hosted, zcloud, vps]",
                         "runs-on: " + "$" + "{{ fromJSON(needs.detect.outputs.runner) }}")
JOB_CANCEL = SAFE.replace("  cancel-in-progress: false\n", "").replace(
    "    runs-on: ubuntu-latest",
    "    concurrency:\n      group: shared-live-service\n"
    "      cancel-in-progress: true\n    runs-on: [self-hosted, zcloud, vps]"
)


class PolicyTest(unittest.TestCase):
    def test_hosted_global_cancel_is_safe(self):
        hosted = SAFE.replace("cancel-in-progress: false", "cancel-in-progress: true")
        self.assertEqual([], scanner.risks(hosted))

    def test_selfhost_global_cancel_denied(self):
        self.assertTrue(scanner.risks(CANCEL))

    def test_manual_selfhost_global_cancel_denied(self):
        self.assertTrue(scanner.risks(CANCEL.replace("pull_request:", "workflow_dispatch:")))

    def test_non_cancelling_selfhost_allowed(self):
        self.assertEqual([], scanner.risks(SELFHOST))

    def test_global_concurrency_after_jobs_is_checked(self):
        variant = SELFHOST.replace(
            "concurrency:\n  group: isolated\n  cancel-in-progress: false\n", ""
        ) + "\nconcurrency:\n  group: shared\n  cancel-in-progress: true\n"
        self.assertTrue(scanner.risks(variant))

    def test_inline_global_cancel_refused(self):
        variant = SELFHOST.replace(
            "concurrency:\n  group: isolated\n  cancel-in-progress: false",
            "concurrency: {group: shared, cancel-in-progress: true}",
        )
        self.assertTrue(scanner.risks(variant))

    def test_inline_job_cancel_refused(self):
        variant = SELFHOST.replace(
            "    runs-on: [self-hosted, zcloud, vps]",
            "    concurrency: {group: shared, cancel-in-progress: true}\n"
            "    runs-on: [self-hosted, zcloud, vps]",
        )
        self.assertTrue(scanner.risks(variant))

    def test_comment_does_not_enable_cancellation(self):
        variant = SELFHOST + "\n# cancel-in-progress: true\n"
        self.assertEqual([], scanner.risks(variant))

    def test_quoted_true_denied(self):
        self.assertTrue(scanner.risks(
            CANCEL.replace("cancel-in-progress: true", "cancel-in-progress: 'true'")
        ))

    def test_expression_denied(self):
        self.assertTrue(scanner.risks(CANCEL.replace(
            "cancel-in-progress: true",
            "cancel-in-progress: " + "$" + "{{ github.ref == 'refs/heads/main' }}"
        )))

    def test_dynamic_runner_denied(self):
        self.assertTrue(scanner.risks(DYNAMIC))

    def test_reusable_job_denied(self):
        self.assertTrue(scanner.risks(CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "uses: ./workflow.yml"
        )))

    def test_job_concurrency_denied(self):
        self.assertTrue(scanner.risks(JOB_CANCEL))

    def test_hosted_job_concurrency_allowed(self):
        hosted = JOB_CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: ubuntu-24.04"
        )
        self.assertEqual([], scanner.risks(hosted))

    def test_duplicate_job_denied(self):
        duplicated = SAFE.replace("jobs:\n", "jobs:\n  check:\n    runs-on: ubuntu-latest\n")
        self.assertRaises(scanner.Unverifiable, scanner.risks, duplicated)

    def test_missing_trigger_denied(self):
        self.assertRaises(scanner.Unverifiable, scanner.risks,
                          SAFE.replace("on:\n", "trigger:\n"))

    def test_yaml_alias_denied(self):
        self.assertRaises(scanner.Unverifiable, scanner.risks,
                          SAFE.replace("runs-on: ubuntu-latest", "runs-on: *runner"))


class GitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ".github/workflows/cancellation.yml"
        self.path.parent.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.root, stderr=subprocess.DEVNULL
        ).decode().strip()

    def commit(self, content):
        self.path.write_text(content, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_new_risk_denied(self):
        base = self.commit(SAFE)
        head = self.commit(CANCEL)
        self.assertEqual([".github/workflows/cancellation.yml"],
                         scanner.evaluate(self.root, base, head))

    def test_modified_legacy_risk_denied(self):
        base = self.commit(CANCEL)
        head = self.commit(CANCEL + "# unrelated edit\n")
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_safe_reduction_passes(self):
        base = self.commit(CANCEL)
        head = self.commit(SELFHOST)
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_hosted_change_passes(self):
        base = self.commit(SAFE)
        head = self.commit(SAFE.replace("echo safe", "echo tested"))
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_symlink_blob_denied(self):
        base = self.commit(SAFE)
        self.path.unlink()
        self.path.symlink_to("/etc/passwd")
        self.git("add", "-A")
        self.git("commit", "-qm", "symlink")
        self.assertRaises(scanner.Unverifiable, scanner.evaluate, self.root,
                          base, self.git("rev-parse", "HEAD"))

    def test_ambiguous_revision_denied(self):
        head = self.commit(SAFE)
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, "main", head)

    def test_output_denies_authority_without_raw_steps(self):
        from io import StringIO
        from contextlib import redirect_stdout
        base = self.commit(SAFE)
        head = self.commit(CANCEL)
        output = StringIO()
        with redirect_stdout(output):
            status = scanner.main(["--base", base, "--head", head,
                                   "--repo-root", str(self.root)])
        self.assertEqual(1, status)
        self.assertIn("VPS_MUTATION_AUTHORIZED=false", output.getvalue())
        self.assertNotIn("echo safe", output.getvalue())


if __name__ == "__main__":
    unittest.main()
