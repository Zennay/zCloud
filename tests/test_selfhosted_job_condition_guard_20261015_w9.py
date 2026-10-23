"""Git-backed negative tests for self-hosted job-level status-gate bypass."""
from __future__ import annotations

import json
import os
from unittest.mock import patch
from scripts import zcloud_selfhosted_job_condition_guard_20261015_w9 as guard
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_selfhosted_job_condition_guard_20261015_w9.py"
FILE = ".github/workflows/gated.yml"


def workflow(job_condition: str | None, runner: str = "[self-hosted, zcloud, vps]", *,
             step_condition: str | None = None) -> str:
    job_if = f"    if: {job_condition}\n" if job_condition is not None else ""
    step_if = f"        if: {step_condition}\n" if step_condition is not None else ""
    return (
        "name: gated\non: push\njobs:\n"
        "  admission:\n    runs-on: ubuntu-latest\n    steps:\n      - run: exit 1\n"
        "  deploy:\n    needs: admission\n"
        + f"    runs-on: {runner}\n" + job_if
        + "    steps:\n      - name: candidate\n" + step_if + "        run: echo safe\n"
    )


class JobConditionGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Source Test")
        self.git("config", "user.email", "source@example.invalid")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, timeout=12,
            text=True, capture_output=True,
        ).stdout.strip()

    def write(self, path, text):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def case(self, before, after, *, unrelated=False):
        if before is None:
            self.write("README.txt", "initial\n")
        else:
            self.write(FILE, before)
        base = self.commit()
        if after is not None:
            self.write(FILE, after)
        else:
            (self.repo / FILE).unlink()
        if unrelated:
            self.write("docs.txt", "separate\n")
        head = self.commit()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo),
             "--base", base, "--head", head],
            capture_output=True, text=True, timeout=18,
        )
        return proc.returncode, json.loads(proc.stdout)

    def test_new_selfhosted_always_condition_denied(self):
        rc, report = self.case(None, workflow("always()"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_new_selfhosted_expression_always_denied(self):
        rc, report = self.case(None, workflow("${{ always() && github.actor == 'Zennay' }}"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_new_failure_condition_denied(self):
        rc, report = self.case(None, workflow("failure()"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_new_cancelled_condition_denied(self):
        rc, report = self.case(None, workflow("cancelled()"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_new_not_cancelled_condition_denied(self):
        rc, report = self.case(None, workflow("!cancelled()"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_or_expression_denied(self):
        rc, report = self.case(None, workflow("success() || github.actor == 'Zennay'"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_default_no_if_is_allowed(self):
        rc, report = self.case(None, workflow(None))
        self.assertEqual((rc, report["new_risks"]), (0, 0))
        self.assertIs(report["mutation_performed"], False)

    def test_success_only_is_allowed(self):
        rc, report = self.case(None, workflow("success()"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_owner_condition_inherits_success(self):
        rc, report = self.case(None, workflow("github.actor == 'Zennay'"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_step_only_always_does_not_bypass_job_needs(self):
        rc, report = self.case(None, workflow(None, step_condition="always()"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_github_hosted_always_not_selfhosted_risk(self):
        rc, report = self.case(None, workflow("always()", runner="ubuntu-latest"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_ubuntu_custom_runner_always_denied(self):
        rc, report = self.case(None, workflow("always()", runner="ubuntu-custom"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_windows_private_runner_failure_denied(self):
        rc, report = self.case(None, workflow("failure()", runner="windows-private"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_macos_private_runner_always_denied(self):
        rc, report = self.case(None, workflow("always()", runner="macos-enterprise"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_standard_windows_hosted_runner_exempt(self):
        rc, report = self.case(None, workflow("always()", runner="windows-2025"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_unquoted_hash_private_runner_status_bypass_denied(self):
        rc, report = self.case(None, workflow("always()", runner="ubuntu-latest#private"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_quoted_hash_private_runner_status_bypass_denied(self):
        rc, report = self.case(None, workflow("always()", runner="'ubuntu-latest#private'"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_quoted_spaced_hash_private_runner_status_bypass_denied(self):
        rc, report = self.case(None, workflow("always()", runner='"ubuntu-latest #private"'))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_hosted_runner_yaml_comment_remains_exempt(self):
        rc, report = self.case(None, workflow("always()", runner="ubuntu-latest # hosted"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_dynamic_runner_always_denied(self):
        rc, report = self.case(None, workflow("always()", runner="${{ inputs.runner }}"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_folded_multiline_always_denied(self):
        w = workflow(">-${{ always() }}")
        rc, report = self.case(None, w)
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_legacy_exact_job_condition_unmodified(self):
        old = workflow("always()")
        rc, report = self.case(old, old.replace("echo safe", "echo same-gate"))
        self.assertEqual((rc, report["legacy_risks"], report["new_risks"]), (0, 1, 0))

    def test_removing_unsafe_job_condition_allowed(self):
        rc, report = self.case(workflow("always()"), workflow("success()"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_adding_new_unsafe_job_denied(self):
        original = workflow(None)
        rc, report = self.case(original, original + (
            "  unguarded:\n    runs-on: [self-hosted, zcloud]\n"
            "    if: always()\n    steps:\n      - run: echo unsafe\n"
        ))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_quoted_job_if_fails_closed(self):
        rc, report = self.case(None, workflow("always()").replace("    if:", "    'if':"))
        self.assertEqual((rc, report.get("reason")), (1, "quoted_job_property"))

    def test_duplicate_job_condition_fails_closed(self):
        rc, report = self.case(None, workflow("success()").replace(
            "    if: success()", "    if: success()\n    if: always()"
        ))
        self.assertEqual((rc, report.get("reason")), (1, "duplicate_job_property"))

    def test_unrelated_changes_do_not_recheck_legacy(self):
        unsafe = workflow("always()")
        rc, report = self.case(unsafe, unsafe, unrelated=True)
        self.assertEqual((rc, report["changed_workflows"]), (0, 0))

    def test_removing_legacy_workflow_allowed(self):
        rc, report = self.case(workflow("always()"), None)
        self.assertEqual((rc, report["changed_workflows"]), (0, 0))

    def test_numeric_false_success_comparison_denied(self):
        rc, report = self.case(None, workflow("${{ success() == 0 }}"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_null_success_comparison_denied(self):
        rc, report = self.case(None, workflow("${{ success() == null }}"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_nonleading_success_syntactic_form_fails_closed(self):
        rc, report = self.case(None, workflow("github.actor == 'Zennay' && success()"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_positive_success_and_actor_guard_allowed(self):
        rc, report = self.case(None, workflow("${{ success() && github.actor == 'Zennay' }}"))
        self.assertEqual((rc, report["new_risks"]), (0, 0))

    def test_success_equals_false_bypasses_failed_admission(self):
        rc, report = self.case(None, workflow("${{ success() == false }}"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_success_not_equal_true_bypasses_failed_admission(self):
        rc, report = self.case(None, workflow("success() != true"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_inverted_success_operand_order_is_denied(self):
        rc, report = self.case(None, workflow("false == success()"))
        self.assertEqual((rc, report["new_risks"]), (1, 1))

    def test_yaml_job_merge_cannot_hide_status_override(self):
        initial = workflow(None)
        new = initial.replace("    needs: admission", "    <<: *unsafe_job\n    needs: admission")
        rc, report = self.case(None, new)
        self.assertEqual((rc, report.get("reason")), (1, "opaque_job_merge"))

    def test_invalid_commit_rejected(self):
        self.write(FILE, workflow("always()"))
        head = self.commit()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo),
             "--base", "invalid", "--head", head],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["reason"], "invalid_sha")


class GitReaderBoundedTests(unittest.TestCase):
    """Source-check Git calls must bound output and descendants before buffering."""

    def run_fake_git(self, script: str):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "git"
            fake.write_text("#!/usr/bin/env python3\n" + script, encoding="utf-8")
            fake.chmod(0o755)
            with patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                with patch.object(guard, "GIT_TIMEOUT", 0.35):
                    guard.git(Path(tmp), "synthetic")

    def test_source_output_cap_enforced_while_streaming(self):
        with self.assertRaisesRegex(guard.AdmissionDenied, "git_response_too_large"):
            self.run_fake_git("import os\nos.write(1, b'A' * 300000)\n")

    def test_forked_stdout_holder_timeout_kills_group(self):
        with self.assertRaisesRegex(guard.AdmissionDenied, "git_unavailable"):
            self.run_fake_git(
                "import subprocess, sys\n"
                "subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(10)'])\n"
            )


if __name__ == "__main__":
    unittest.main()
