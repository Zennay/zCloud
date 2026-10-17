"""Real-Git regressions for default fail-fast on self-hosted matrix jobs."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_selfhosted_matrix_failfast_ratchet_20261015_w8.py"
WORKFLOW = ".github/workflows/matrix-proof.yml"


def yaml_matrix(runner="[self-hosted, zcloud, vps]", fast=None, *,
                matrix=True, strategy_override=None, extra_steps="") -> str:
    policy = (
        f"    strategy: {strategy_override}\n" if strategy_override is not None
        else "    strategy:\n" + ("      matrix:\n        python: ['3.11', '3.12']\n" if matrix else "")
        + (f"      fail-fast: {fast}\n" if fast is not None else "")
    )
    return (
        "name: proof\non: push\njobs:\n  matrix_proof:\n"
        f"    runs-on: {runner}\n{policy}"
        "    steps:\n      - run: echo ok\n"
        + extra_steps
    )


class MatrixFailFastGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI Test")
        self.git("config", "user.email", "ci@example.invalid")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True,
            timeout=12, check=True,
        ).stdout.strip()

    def write(self, content, path=WORKFLOW):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "sample")
        return self.git("rev-parse", "HEAD")

    def case(self, before, after, *, unrelated=False):
        if before is None:
            self.write("initial\n", "README.txt")
        else:
            self.write(before)
        base = self.commit()
        if after is not None:
            self.write(after)
        else:
            (self.repo / WORKFLOW).unlink()
        if unrelated:
            self.write("updated\n", "details.txt")
        head = self.commit()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo),
             "--base", base, "--head", head],
            capture_output=True, text=True, timeout=18,
        )
        return proc.returncode, json.loads(proc.stdout)

    def test_new_selfhosted_matrix_without_failfast_is_denied(self):
        rc, data = self.case(None, yaml_matrix())
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_explicit_selfhosted_failfast_true_is_denied(self):
        rc, data = self.case(None, yaml_matrix(fast="true"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_safe_selfhosted_matrix_is_allowed(self):
        rc, data = self.case(None, yaml_matrix(fast="false"))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))
        self.assertIs(data["mutation_performed"], False)

    def test_hosted_matrix_default_does_not_block(self):
        rc, data = self.case(None, yaml_matrix(runner="ubuntu-latest"))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_windows_hosted_matrix_default_does_not_block(self):
        rc, data = self.case(None, yaml_matrix(runner="windows-2025"))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_ubuntu_custom_label_is_not_assumed_github_hosted(self):
        rc, data = self.case(None, yaml_matrix(runner="ubuntu-custom"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_windows_private_label_is_not_assumed_github_hosted(self):
        rc, data = self.case(None, yaml_matrix(runner="windows-private"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_macos_enterprise_label_is_not_assumed_github_hosted(self):
        rc, data = self.case(None, yaml_matrix(runner="macos-enterprise"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_known_ubuntu_hosted_label_stays_exempt(self):
        rc, data = self.case(None, yaml_matrix(runner="ubuntu-24.04"))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_unquoted_hash_private_runner_matrix_is_denied(self):
        rc, data = self.case(None, yaml_matrix(runner="ubuntu-latest#private"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_quoted_hash_private_runner_matrix_is_denied(self):
        rc, data = self.case(None, yaml_matrix(runner="'ubuntu-latest#private'"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_quoted_spaced_hash_private_runner_matrix_is_denied(self):
        rc, data = self.case(None, yaml_matrix(runner='"ubuntu-latest #private"'))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_hosted_runner_yaml_comment_stays_exempt(self):
        rc, data = self.case(None, yaml_matrix(runner="ubuntu-latest # hosted"))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_dynamic_runner_default_is_denied(self):
        rc, data = self.case(None, yaml_matrix(runner="${{ inputs.runner }}"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_dynamic_failfast_value_is_denied(self):
        rc, data = self.case(None, yaml_matrix(fast="${{ inputs.fail_fast }}"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_no_matrix_cannot_cancel_sibling(self):
        rc, data = self.case(None, yaml_matrix(matrix=False))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_untrusted_dynamic_strategy_is_denied(self):
        rc, data = self.case(None, yaml_matrix(strategy_override="${{ inputs.strategy }}"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_inline_matrix_is_denied(self):
        rc, data = self.case(None, yaml_matrix(strategy_override="{matrix: {os: [ubuntu-latest]}, fail-fast: false}"))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_legacy_default_is_grandfathered_when_steps_change(self):
        old = yaml_matrix()
        new = old.replace("echo ok", "echo changed")
        rc, data = self.case(old, new)
        self.assertEqual((rc, data["legacy_risks"], data["introduced_risks"]), (0, 1, 0))

    def test_legacy_default_can_be_fixed(self):
        rc, data = self.case(yaml_matrix(), yaml_matrix(fast="false"))
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_new_unsafe_second_job_is_denied(self):
        first = yaml_matrix()
        new = first + (
            "  another:\n    runs-on: [self-hosted, vps]\n"
            "    strategy:\n      matrix:\n        component: [a, b]\n"
            "    steps:\n      - run: echo second\n"
        )
        rc, data = self.case(first, new)
        self.assertEqual((rc, data["legacy_risks"], data["introduced_risks"]), (1, 1, 1))

    def test_comment_not_interpreted_as_matrix(self):
        source = yaml_matrix(matrix=False, extra_steps="      # matrix: [x, y]\n")
        rc, data = self.case(None, source)
        self.assertEqual((rc, data["introduced_risks"]), (0, 0))

    def test_non_workflow_change_carries_no_new_debt(self):
        old = yaml_matrix()
        rc, data = self.case(old, old, unrelated=True)
        self.assertEqual((rc, data["changed_workflows"]), (0, 0))

    def test_removing_unsafe_workflow_allowed(self):
        rc, data = self.case(yaml_matrix(), None)
        self.assertEqual((rc, data["changed_workflows"]), (0, 0))

    def test_top_level_inline_jobs_mapping_is_denied(self):
        yaml_text = (
            "name: test\non: push\n"
            "jobs: { matrix_proof: { runs-on: self-hosted, "
            "strategy: { matrix: {component: [a, b]} } } }\n"
        )
        rc, data = self.case(None, yaml_text)
        self.assertEqual((rc, data.get("reason")), (1, "unsupported_jobs_mapping"))

    def test_quoted_jobs_key_is_denied(self):
        rc, data = self.case(None, yaml_matrix().replace("jobs:", "'jobs':"))
        self.assertEqual((rc, data.get("reason")), (1, "unsupported_jobs_mapping"))

    def test_quoted_job_name_is_denied(self):
        rc, data = self.case(None, yaml_matrix().replace("  matrix_proof:", "  'matrix_proof':"))
        self.assertEqual((rc, data.get("reason")), (1, "unsupported_job_identifier"))

    def test_quoted_strategy_key_is_denied(self):
        rc, data = self.case(None, yaml_matrix(fast="false").replace("    strategy:", "    'strategy':"))
        self.assertEqual((rc, data.get("reason")), (1, "ambiguous_quoted_job_key"))

    def test_quoted_matrix_key_is_denied(self):
        rc, data = self.case(None, yaml_matrix(fast="false").replace("      matrix:", "      'matrix':"))
        self.assertEqual((rc, data.get("reason")), (1, "ambiguous_quoted_job_key"))

    def test_quoted_failfast_key_is_denied(self):
        rc, data = self.case(None, yaml_matrix(fast="false").replace("      fail-fast:", "      'fail-fast':"))
        self.assertEqual((rc, data.get("reason")), (1, "ambiguous_quoted_job_key"))

    def test_strategy_alias_merged_matrix_is_denied(self):
        rc, data = self.case(None, yaml_matrix(matrix=False).replace(
            "    strategy:\n", "    strategy:\n      <<: *matrix_strategy\n",
        ))
        self.assertEqual((rc, data["introduced_risks"]), (1, 1))

    def test_duplicate_failfast_key_is_denied(self):
        rc, data = self.case(None, yaml_matrix(fast="false").replace(
            "      fail-fast: false", "      fail-fast: false\n      fail-fast: true",
        ))
        self.assertEqual((rc, data.get("reason")), (1, "duplicate_job_key"))

    def test_invalid_revision_rejected(self):
        self.write(yaml_matrix())
        head = self.commit()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo),
             "--base", "garbage", "--head", head],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["reason"], "invalid_revision")

    def test_symlink_workflow_is_rejected(self):
        self.write("initial\n", "README.txt")
        base = self.commit()
        dest = self.repo / WORKFLOW
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(self.repo / "README.txt")
        head = self.commit()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo),
             "--base", base, "--head", head],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["reason"], "unsafe_workflow_file_type")


if __name__ == "__main__":
    unittest.main()
