"""Real-Git tests for deny-only self-hosted cancellation admission."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/zcloud_selfhosted_cancel_ratchet_20261015_w7.py"
PATH = ".github/workflows/example.yml"


def workflow(runner: str, cancellation: str, *, extra: str = "") -> str:
    return (
        f"name: test\non: push\nconcurrency:\n"
        f"  group: test-ci\n  cancel-in-progress: {cancellation}\n"
        f"jobs:\n  check:\n    runs-on: {runner}\n"
        f"    steps:\n      - run: echo ok\n{extra}"
    )


class CancelRatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True,
            capture_output=True, text=True, timeout=12,
        ).stdout.strip()

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        return self.git("rev-parse", "HEAD")

    def probe(self, prior, after, *, other=False):
        if prior is not None:
            self.write(PATH, prior)
        else:
            self.write("README.txt", "initial\n")
        base = self.commit()
        if after is not None:
            self.write(PATH, after)
        else:
            (self.root / PATH).unlink()
        if other:
            self.write("note.txt", "edited\n")
        head = self.commit()
        done = subprocess.run(
            [sys.executable, str(SOURCE), "--base", base, "--head", head,
             "--repo-root", str(self.root)],
            capture_output=True, text=True, timeout=15,
        )
        return done.returncode, json.loads(done.stdout)

    def test_new_selfhosted_true_is_denied(self):
        code, report = self.probe(None, workflow("[self-hosted, zcloud, vps]", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_new_selfhosted_false_is_safe(self):
        code, report = self.probe(None, workflow("[self-hosted, zcloud, vps]", "false"))
        self.assertEqual((code, report["new_unsafe"]), (0, 0))

    def test_hosted_only_true_does_not_affect_vps(self):
        code, report = self.probe(None, workflow("ubuntu-latest", "true"))
        self.assertEqual((code, report["new_unsafe"]), (0, 0))

    def test_ubuntu_custom_label_can_be_selfhosted_and_is_denied(self):
        code, report = self.probe(None, workflow("ubuntu-custom", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_windows_private_label_can_be_selfhosted_and_is_denied(self):
        code, report = self.probe(None, workflow("windows-private", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_macos_enterprise_label_can_be_selfhosted_and_is_denied(self):
        code, report = self.probe(None, workflow("macos-enterprise", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_known_hosted_windows_runner_is_exempt(self):
        code, report = self.probe(None, workflow("windows-2025", "true"))
        self.assertEqual((code, report["new_unsafe"]), (0, 0))

    def test_unknown_quoted_runner_key_is_conservatively_denied(self):
        yaml_source = workflow("ubuntu-custom", "true").replace(
            "runs-on:", "'runs-on':"
        )
        code, report = self.probe(None, yaml_source)
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_unquoted_inline_hash_runner_label_cannot_spoof_hosted(self):
        code, report = self.probe(None, workflow("ubuntu-latest#private", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_quoted_hash_runner_label_cannot_spoof_hosted(self):
        code, report = self.probe(None, workflow("'ubuntu-latest#private'", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_quoted_spaced_hash_runner_label_cannot_spoof_hosted(self):
        code, report = self.probe(None, workflow('"ubuntu-latest #private"', "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_real_yaml_comment_on_hosted_runner_is_ignored(self):
        safe = workflow("ubuntu-latest # trusted hosted runner", "true")
        code, report = self.probe(None, safe)
        self.assertEqual((code, report["new_unsafe"]), (0, 0))

    def test_reusable_job_with_spoofed_with_input_is_not_hosted(self):
        delegated = (
            "name: delegated\non: workflow_dispatch\n"
            "concurrency:\n  group: delegation\n  cancel-in-progress: true\n"
            "jobs:\n  delegate:\n    uses: ./.github/workflows/vps.yml\n"
            "    with:\n      pretend-runs-on: ubuntu-latest\n"
        )
        code, report = self.probe(None, delegated)
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_shell_text_cannot_spoof_job_runner_identity(self):
        text = (
            "name: delegated\non: workflow_dispatch\n"
            "concurrency:\n  group: delegation\n  cancel-in-progress: true\n"
            "jobs:\n  delegate:\n    uses: ./.github/workflows/vps.yml\n"
            "    with:\n      script: 'echo runs-on: ubuntu-latest'\n"
        )
        code, report = self.probe(None, text)
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_dynamic_runner_true_is_denied(self):
        code, report = self.probe(None, workflow("${{ inputs.runner }}", "true"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_dynamic_cancel_is_denied(self):
        code, report = self.probe(None, workflow("[self-hosted, vps]", "${{ inputs.cancel }}"))
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_existing_unsafe_unchanged_is_grandfathered(self):
        unsafe = workflow("[self-hosted, zcloud]", "true")
        code, report = self.probe(unsafe, unsafe + "# trailing comment\n")
        self.assertEqual((code, report["legacy_unsafe"], report["new_unsafe"]), (0, 1, 0))

    def test_legacy_safety_remediation_is_allowed(self):
        code, report = self.probe(
            workflow("[self-hosted, zcloud]", "true"),
            workflow("[self-hosted, zcloud]", "false"),
        )
        self.assertEqual((code, report["new_unsafe"]), (0, 0))

    def test_new_unsafe_second_occurrence_denied(self):
        unsafe = workflow("[self-hosted, zcloud]", "true")
        extra = "  other:\n    runs-on: [self-hosted, zcloud]\n    concurrency:\n      group: other\n      cancel-in-progress: true\n"
        code, report = self.probe(unsafe, unsafe + extra)
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_no_workflow_diff_is_allowed(self):
        safe = workflow("[self-hosted, zcloud]", "true")
        code, report = self.probe(safe, safe, other=True)
        self.assertEqual((code, report["changed_workflows"]), (0, 0))

    def test_workflow_deletion_is_allowed(self):
        code, report = self.probe(workflow("[self-hosted, zcloud]", "true"), None)
        self.assertEqual((code, report["changed_workflows"]), (0, 0))

    def test_inline_concurrency_mapping_is_denied(self):
        inline = workflow("[self-hosted, vps]", "false").replace(
            "concurrency:\n  group: test-ci\n  cancel-in-progress: false\n",
            "concurrency: { group: test-ci, cancel-in-progress: true }\n",
        )
        code, report = self.probe(None, inline)
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_quoted_yaml_cancel_key_is_denied(self):
        quoted = workflow("[self-hosted, vps]", "true").replace(
            "cancel-in-progress: true", "'cancel-in-progress': true"
        )
        code, report = self.probe(None, quoted)
        self.assertEqual((code, report["new_unsafe"]), (1, 1))

    def test_bad_revision_fails_without_mutation(self):
        self.write(PATH, workflow("[self-hosted]", "true"))
        self.commit()
        head = self.git("rev-parse", "HEAD")
        run = subprocess.run(
            [sys.executable, str(SOURCE), "--base", "invalid", "--head", head,
             "--repo-root", str(self.root)], capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["reason"], "invalid_commit_identity")


if __name__ == "__main__":
    unittest.main()
