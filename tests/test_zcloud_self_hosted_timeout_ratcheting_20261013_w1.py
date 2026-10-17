"""Independent deploy-ops ratchet regressions (no overlap with timeout-workflow owner)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_self_hosted_timeout_audit as audit


class TimeoutDebtRatchetingTests(unittest.TestCase):
    def test_dynamic_runner_and_yaml_alias_are_never_silent(self):
        source = """jobs:
  dynamic:
    runs-on: ${{ matrix.runner }}
    timeout-minutes: 5
  alias:
    runs-on: *private_runner
    timeout-minutes: 10
  hosted:
    runs-on: ubuntu-latest
    timeout-minutes: 50
  misleading:
    runs-on: not-self-hosted
    timeout-minutes: 99
"""
        rows = audit.inspect_workflow(".github/workflows/dynamic.yml", source, max_minutes=30)
        self.assertEqual(["dynamic", "alias", "misleading"], [item.job for item in rows])
        self.assertEqual(["dynamic_runner_unknown", "dynamic_runner_unknown", "runner_target_unknown"], [item.status for item in rows])
        counts = audit.summarize(rows, max_minutes=30)
        self.assertEqual(3, counts["finding_count"])
        self.assertEqual(2, counts["by_status"]["dynamic_runner_unknown"])
        self.assertEqual(1, counts["by_status"]["runner_target_unknown"])
        self.assertFalse(counts["mutation_performed"])

    def test_moving_git_ref_is_pinned_before_any_snapshot_read(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = root / ".github" / "workflows" / "pinned.yml"
            workflow.parent.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for k, v in (("user.name", "Audit Tests"), ("user.email", "audit@example.invalid")):
                subprocess.run(["git", "-C", str(root), "config", k, v], check=True)
            workflow.write_text(
                "jobs:\n  proof:\n    runs-on: self-hosted\n    timeout-minutes: 10\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(root), "add", ".github"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "bounded"], check=True)
            old_sha = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            workflow.write_text(
                "jobs:\n  proof:\n    runs-on: self-hosted\n    timeout-minutes: 45\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(root), "add", ".github"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "over limit"], check=True)
            new_sha = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()

            original_run = subprocess.run
            flipped = False

            def move_head_after_resolution(args, **kwargs):
                nonlocal flipped
                if "ls-tree" in args and not flipped:
                    flipped = True
                    original_run(["git", "-C", str(root), "update-ref", "HEAD", old_sha], check=True)
                return original_run(args, **kwargs)

            with patch.object(audit.subprocess, "run", side_effect=move_head_after_resolution):
                observed = audit.scan_git_ref(root, "HEAD", max_minutes=30)

            self.assertTrue(flipped)
            self.assertEqual([("proof", 45, "over_limit")],
                             [(x.job, x.timeout_minutes, x.status) for x in observed])
            self.assertEqual(old_sha, subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip())
            self.assertNotEqual(old_sha, new_sha)

    def test_git_baseline_preserves_exact_unusual_workflow_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            names = ("name with space.yml", "tab\tname.yaml", "unicode-épreuve.yml", "line\nbreak.yml")
            for position, name in enumerate(names):
                (workflows / name).write_text(
                    f"jobs:\n  proof_{position}:\n    runs-on: self-hosted\n"
                    "    timeout-minutes: 15\n",
                    encoding="utf-8",
                )
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for key, value in (("user.name", "Audit Tests"), ("user.email", "audit@example.invalid")):
                subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
            subprocess.run(["git", "-C", str(root), "add", ".github"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "unusual filenames"], check=True)
            sha = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            self.assertEqual(
                audit.scan_repository(root, max_minutes=30),
                audit.scan_git_ref(root, sha, max_minutes=30),
            )

    def test_git_baseline_only_counts_direct_workflow_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            nested = workflows / "examples"
            nested.mkdir(parents=True)
            (workflows / "active.yml").write_text(
                "jobs:\n  active:\n    runs-on: self-hosted\n    timeout-minutes: 15\n",
                encoding="utf-8",
            )
            (nested / "sample.yml").write_text(
                "jobs:\n  example:\n    runs-on: self-hosted\n    timeout-minutes: 100\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for k, v in (("user.name", "Workflow Audit"), ("user.email", "audit@example.invalid")):
                subprocess.run(["git", "-C", str(root), "config", k, v], check=True)
            subprocess.run(["git", "-C", str(root), "add", ".github"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "workflow snapshot"], check=True)
            sha = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            working = audit.scan_repository(root, max_minutes=30)
            baseline = audit.scan_git_ref(root, sha, max_minutes=30)
            self.assertEqual(working, baseline)
            self.assertEqual([("active", "bounded")], [(x.job, x.status) for x in baseline])

    def test_duplicate_root_jobs_sections_fail_closed(self):
        for first_root, second_root in (("jobs", "jobs"), ("jobs", "'jobs'")):
            with self.subTest(first_root=first_root, second_root=second_root):
                source = (
                    f"{first_root}:\n"
                    "  safe:\n    runs-on: ubuntu-latest\n"
                    f"{second_root}:\n"
                    "  hidden:\n    runs-on: [self-hosted, zcloud]\n"
                    "    timeout-minutes: 200\n"
                )
                with self.assertRaisesRegex(RuntimeError, "duplicate root jobs sections"):
                    audit.inspect_workflow(".github/workflows/roots.yml", source, max_minutes=30)

    def test_duplicate_job_ids_fail_closed_even_with_different_quotes(self):
        source = """jobs:
  worker:
    runs-on: ubuntu-latest
    timeout-minutes: 5
  "worker":
    runs-on: [self-hosted, zcloud]
    timeout-minutes: 120
"""
        with self.assertRaisesRegex(RuntimeError, "duplicate workflow job declaration"):
            audit.inspect_workflow(".github/workflows/job-dup.yml", source, max_minutes=30)

    def test_single_root_and_distinct_job_ids_still_work(self):
        source = """jobs:
  first:
    runs-on: ubuntu-latest
    timeout-minutes: 40
  second:
    runs-on: [self-hosted, zcloud]
    timeout-minutes: 20
"""
        rows = audit.inspect_workflow("valid.yml", source, max_minutes=30)
        self.assertEqual([("second", "bounded")], [(r.job, r.status) for r in rows])

    def test_strict_cli_rejects_mutable_git_ref_but_allows_reporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = root / ".github" / "workflows" / "tiny.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  safe:\n    runs-on: self-hosted\n    timeout-minutes: 5\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for key, val in (("user.name", "Guard Test"), ("user.email", "guard@example.invalid")):
                subprocess.run(["git", "-C", str(root), "config", key, val], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
            sha = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            before = workflow.read_bytes()
            cmd = [
                sys.executable, str(Path(audit.__file__).resolve()),
                "--repo-root", str(root), "--base-ref", "HEAD",
            ]
            regular = subprocess.run(cmd, capture_output=True, text=True, check=False)
            strict = subprocess.run(
                cmd + ["--require-no-new-debt"], capture_output=True, text=True, check=False
            )
            exact = subprocess.run(
                cmd[:-1] + [sha, "--require-no-new-debt"],
                capture_output=True, text=True, check=False
            )
            self.assertEqual(0, regular.returncode, regular.stderr)
            self.assertEqual(2, strict.returncode, strict.stderr)
            self.assertEqual(
                "strict_base_ref_must_be_full_immutable_sha",
                json.loads(strict.stdout)["error"],
            )
            self.assertEqual(0, exact.returncode, exact.stderr)
            self.assertEqual(0, json.loads(exact.stdout)["new_debt_count"])
            self.assertEqual(before, workflow.read_bytes())

    def test_compact_yaml_job_mapping_and_alias_cannot_escape_audit(self):
        source = """jobs:
  compact: {runs-on: [self-hosted, zcloud, vps], timeout-minutes: 90}
  reference: *private_job
  ordinary:
    runs-on: ubuntu-latest
    timeout-minutes: 90
"""
        rows = audit.inspect_workflow(".github/workflows/compact.yml", source, max_minutes=30)
        self.assertEqual(["compact", "reference"], [row.job for row in rows])
        self.assertTrue(all(row.status == "dynamic_runner_unknown" for row in rows))
        self.assertEqual(2, audit.summarize(rows, max_minutes=30)["finding_count"])

    def test_root_compact_jobs_mapping_denied_instead_of_empty_inventory(self):
        source = "jobs: {private: {runs-on: self-hosted, timeout-minutes: 90}}\n"
        with self.assertRaisesRegex(RuntimeError, "inline root jobs"):
            audit.inspect_workflow(".github/workflows/root-flow.yml", source, max_minutes=30)

    def test_quoted_job_keys_require_matching_quotes(self):
        source = """jobs:
  'ok':
    runs-on: self-hosted
    timeout-minutes: 10
  'mismatch":
    runs-on: ubuntu-latest
"""
        with self.assertRaisesRegex(RuntimeError, "unparseable workflow job declaration"):
            audit.inspect_workflow(".github/workflows/quotes.yml", source, max_minutes=30)

    def test_incorrectly_named_job_does_not_silently_disappear(self):
        for declaration in ("'broken\":", "with.dot:", "- item:"):
            with self.subTest(declaration=declaration):
                source = "jobs:\n  " + declaration + "\n    runs-on: self-hosted\n"
                with self.assertRaisesRegex(RuntimeError, "unparseable workflow job declaration"):
                    audit.inspect_workflow("bad.yml", source, max_minutes=30)

    def test_ambiguous_duplicate_runner_and_timeout_keys_fail_closed(self):
        source = """jobs:
  duplicate_runner:
    runs-on: ubuntu-latest
    "runs-on": [self-hosted, zcloud]
    timeout-minutes: 10
  duplicate_timeout:
    runs-on: self-hosted
    timeout-minutes: 5
    'timeout-minutes': 120
  healthy_hosted:
    runs-on: windows-2022
    timeout-minutes: 180
"""
        rows = audit.inspect_workflow(".github/workflows/duplicates.yml", source, max_minutes=30)
        self.assertEqual(
            [("duplicate_runner", "dynamic_runner_unknown"), ("duplicate_timeout", "dynamic_or_invalid")],
            [(row.job, row.status) for row in rows],
        )
        self.assertTrue(all(row.status != "bounded" for row in rows))

    def test_quoted_yaml_keys_cannot_hide_timeout_violation(self):
        source = """'jobs':
    "private-job":
        "runs-on": [self-hosted, zcloud, vps]
        'timeout-minutes': 45
    'hosted-job':
        "runs-on": ubuntu-latest
        "timeout-minutes": 120
"""
        rows = audit.inspect_workflow(".github/workflows/quoted.yml", source, max_minutes=30)
        self.assertEqual([("private-job", "over_limit")], [(r.job, r.status) for r in rows])

    def test_inherited_inline_or_sequence_yaml_merge_runner_cannot_escape(self):
        source = """defaults: &private
  runs-on: [self-hosted, zcloud, vps]
jobs:
  mapping_merge:
    <<: {runs-on: [self-hosted, zcloud, vps]}
    timeout-minutes: 5
  sequence_merge:
    <<: [*private]
    timeout-minutes: 10
  explicit_hosted_override:
    <<: *private
    runs-on: ubuntu-latest
    timeout-minutes: 200
"""
        rows = audit.inspect_workflow("merge-shapes.yml", source, max_minutes=30)
        self.assertEqual(
            [("mapping_merge", "dynamic_runner_unknown"),
             ("sequence_merge", "dynamic_runner_unknown")],
            [(row.job, row.status) for row in rows],
        )

    def test_yaml_merge_alias_cannot_hide_inherited_runner(self):
        source = """defaults: &private
  runs-on: [self-hosted, zcloud, vps]
jobs:
  inherited:
    <<: *private
    timeout-minutes: 5
"""
        rows = audit.inspect_workflow(".github/workflows/merge.yml", source, max_minutes=30)
        self.assertEqual([("inherited", "dynamic_runner_unknown")], [(r.job, r.status) for r in rows])

    def test_valid_alternate_yaml_indentation_cannot_hide_private_runner(self):
        source = """jobs:
    deeper:
        runs-on: [self-hosted, zcloud, vps]
        timeout-minutes: 75
    custom:
        runs-on: [zcloud, vps]
        timeout-minutes: 5
"""
        rows = audit.inspect_workflow(".github/workflows/deeper.yml", source, max_minutes=30)
        self.assertEqual(["deeper", "custom"], [row.job for row in rows])
        self.assertEqual(["over_limit", "runner_target_unknown"], [row.status for row in rows])

    def test_alternate_child_key_indentation_does_not_match_nested_script(self):
        source = """jobs:
  proof:
      runs-on: ubuntu-latest
      steps:
        - run: |
            echo runs-on: self-hosted
            echo timeout-minutes: 999
  private:
      name: private runner proof
      runs-on: my-special-runner
      timeout-minutes: 45
"""
        rows = audit.inspect_workflow(".github/workflows/alternate.yml", source, max_minutes=30)
        self.assertEqual(["private"], [row.job for row in rows])
        self.assertEqual(["runner_target_unknown"], [row.status for row in rows])

    def test_custom_label_and_group_runner_are_never_assumed_hosted(self):
        source = """jobs:
  custom:
    runs-on: [zcloud, vps]
    timeout-minutes: 120
  group:
    runs-on:
      group: private-vps-group
      labels: [zcloud, vps]
    timeout-minutes: 5
  single_custom:
    runs-on: private-vps-label
    timeout-minutes: 5
  official:
    runs-on: ubuntu-24.04-arm
    timeout-minutes: 50
  official_list:
    runs-on: [windows-2022]
    timeout-minutes: 50
"""
        rows = audit.inspect_workflow(".github/workflows/labels.yml", source, max_minutes=30)
        self.assertEqual(["custom", "group", "single_custom"], [row.job for row in rows])
        self.assertTrue(all(row.status == "runner_target_unknown" for row in rows))
        self.assertEqual(3, audit.summarize(rows, max_minutes=30)["by_status"]["runner_target_unknown"])

    def test_oversized_workflow_denied_before_processing_live_and_git_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wfdir = root / ".github" / "workflows"
            wfdir.mkdir(parents=True)
            file = wfdir / "oversized.yml"
            file.write_text(
                "jobs:\n  x:\n    runs-on: self-hosted\n" + " " * (audit.MAX_WORKFLOW_FILE_BYTES + 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "size exceeds limit"):
                audit.scan_repository(root, max_minutes=30)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for key, value in (("user.email", "audit@example.invalid"), ("user.name", "Audit Test")):
                subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
            subprocess.run(["git", "-C", str(root), "add", ".github"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "oversized workflow"], check=True)
            sha = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            with self.assertRaisesRegex(RuntimeError, "size exceeds limit"):
                audit.scan_git_ref(root, sha, max_minutes=30)

    def test_empty_workflow_jobs_mapping_cannot_appear_green(self):
        for source in (
            "jobs:\n",
            "jobs:\n  # deliberately empty\n",
            "jobs:\n\n# only comments\n",
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(RuntimeError, "no auditable jobs"):
                    audit.inspect_workflow("empty.yml", source, max_minutes=30)

        hosted_only = "jobs:\n  hosted:\n    runs-on: ubuntu-latest\n"
        self.assertEqual([], audit.inspect_workflow("hosted.yml", hosted_only, max_minutes=30))

    def test_workflow_missing_jobs_root_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "jobs root missing"):
            audit.inspect_workflow("rootless.yml", "name: rootless\non: push\n", max_minutes=30)

    def test_workflow_content_mutation_during_read_denies_stale_snapshot(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / ".github" / "workflows"
            directory.mkdir(parents=True)
            file = directory / "dynamic.yml"
            file.write_text(
                "jobs:\n  safe:\n    runs-on: self-hosted\n    timeout-minutes: 5\n",
                encoding="utf-8",
            )
            fstat_orig = audit.os.fstat
            seen = 0

            def changed_after_read(fd):
                nonlocal seen
                seen += 1
                if seen == 2:
                    file.write_text(
                        "jobs:\n  unsafe:\n    runs-on: self-hosted\n"
                        "    timeout-minutes: 600\n", encoding="utf-8"
                    )
                return fstat_orig(fd)

            with patch.object(audit.os, "fstat", side_effect=changed_after_read):
                with self.assertRaisesRegex(RuntimeError, "workflow changed during read"):
                    audit.scan_repository(root, max_minutes=30)

    def test_workflow_reads_pin_both_directories_and_bound_file_descriptor(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / ".github" / "workflows"
            directory.mkdir(parents=True)
            (directory / "proof.yml").write_text(
                "jobs:\n  proof:\n    runs-on: self-hosted\n    timeout-minutes: 10\n",
                encoding="utf-8",
            )
            original_open = audit.os.open
            with patch.object(audit.os, "open", wraps=original_open) as spy:
                rows = audit.scan_repository(root, max_minutes=30)
            self.assertEqual([("proof", "bounded")], [(r.job, r.status) for r in rows])
            self.assertEqual(3, spy.call_count)
            ancestor_flags = spy.call_args_list[0].args[1]
            workflow_flags = spy.call_args_list[1].args[1]
            file_flags = spy.call_args_list[2].args[1]
            self.assertTrue(ancestor_flags & audit.os.O_NOFOLLOW)
            self.assertTrue(ancestor_flags & audit.os.O_DIRECTORY)
            self.assertTrue(workflow_flags & audit.os.O_NOFOLLOW)
            self.assertTrue(workflow_flags & audit.os.O_DIRECTORY)
            self.assertTrue(file_flags & audit.os.O_NOFOLLOW)
            self.assertIn("dir_fd", spy.call_args_list[2].kwargs)

    def test_workflow_directory_substitution_does_not_redirect_read(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / ".github" / "workflows"
            directory.mkdir(parents=True)
            (directory / "proof.yml").write_text(
                "jobs:\n  legitimate:\n    runs-on: self-hosted\n"
                "    timeout-minutes: 10\n", encoding="utf-8",
            )
            external = root / "external"
            external.mkdir()
            (external / "proof.yml").write_text(
                "jobs:\n  swapped:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
            )
            real_listdir = audit.os.listdir

            def replace_directory(fd):
                names = real_listdir(fd)
                directory.rename(root / ".github" / "workflows.original")
                directory.symlink_to(external, target_is_directory=True)
                return names

            with patch.object(audit.os, "listdir", side_effect=replace_directory):
                rows = audit.scan_repository(root, max_minutes=30)
            self.assertEqual([("legitimate", "bounded")], [(r.job, r.status) for r in rows])

    def test_fifo_workflow_is_rejected_without_blocking(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / ".github" / "workflows"
            directory.mkdir(parents=True)
            os.mkfifo(directory / "pipe.yml")
            with self.assertRaisesRegex(RuntimeError, "non-file workflow"):
                audit.scan_repository(root, max_minutes=30)

    def test_workflow_file_count_is_bounded(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wfdir = root / ".github" / "workflows"
            wfdir.mkdir(parents=True)
            for name in ("one.yml", "two.yml"):
                (wfdir / name).write_text(
                    "jobs:\n  hosted:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
                )
            with patch.object(audit, "MAX_WORKFLOW_FILES", 1):
                with self.assertRaisesRegex(RuntimeError, "file count exceeds limit"):
                    audit.scan_repository(root, max_minutes=30)

    def test_git_and_workspace_symlink_ancestors_fail_closed(self):
        for alias in ("github", "workflows"):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                external = root / "external"
                external.mkdir()
                (external / "hosted.yml").write_text(
                    "jobs:\n  prove:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
                )
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                for key, value in (("user.email", "audit@example.invalid"), ("user.name", "Audit Test")):
                    subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
                folder = root / ".github"
                if alias == "github":
                    folder.symlink_to(external, target_is_directory=True)
                else:
                    folder.mkdir()
                    (folder / "workflows").symlink_to(external, target_is_directory=True)
                subprocess.run(["git", "-C", str(root), "add", ".github"], check=True)
                subprocess.run(["git", "-C", str(root), "commit", "-qm", "symlink snapshot"], check=True)
                sha = subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
                ).strip()
                with self.assertRaisesRegex(RuntimeError, "symlink workflow"):
                    audit.scan_repository(root, max_minutes=30)
                with self.assertRaisesRegex(RuntimeError, "symlink or non-tree"):
                    audit.scan_git_ref(root, sha, max_minutes=30)

    def test_oversized_numeric_timeout_literals_are_bounded_and_denied(self):
        huge = "9" * 6000
        for raw in (huge, "-" + huge):
            with self.subTest(sign=raw[0]):
                source = (
                    "jobs:\n"
                    "  oversize:\n"
                    "    runs-on: [self-hosted, zcloud]\n"
                    f"    timeout-minutes: {raw}\n"
                )
                rows = audit.inspect_workflow("huge.yml", source, max_minutes=30)
                self.assertEqual(1, len(rows))
                self.assertEqual("dynamic_or_invalid", rows[0].status)
                self.assertIsNone(rows[0].timeout_minutes)
                self.assertEqual(1, audit.summarize(rows, max_minutes=30)["finding_count"])

    def test_noncanonical_numeric_timeout_scalars_are_never_bounded(self):
        values = ["1_0", "+10", "01", "٠٥", "１０", "-1", "0", "10"]
        workflow = "jobs:\n" + "".join(
            f"  j{i}:\n    runs-on: self-hosted\n    timeout-minutes: {raw}\n"
            for i, raw in enumerate(values)
        )
        rows = audit.inspect_workflow("noncanonical.yml", workflow, max_minutes=30)
        self.assertEqual(
            ["dynamic_or_invalid"] * 5 + ["invalid", "invalid", "bounded"],
            [r.status for r in rows],
        )
        self.assertTrue(all(r.timeout_minutes is None for r in rows[:5]))

    def test_strict_mode_rejects_changes_between_legacy_debt_types(self):
        path = ".github/workflows/legacy.yml"
        missing = audit.TimeoutFinding(path, "legacy", None, "missing")
        dynamic = audit.TimeoutFinding(path, "legacy", None, "dynamic_or_invalid")
        invalid_zero = audit.TimeoutFinding(path, "legacy", 0, "invalid")
        invalid_negative = audit.TimeoutFinding(path, "legacy", -1, "invalid")
        fixed = audit.TimeoutFinding(path, "legacy", 15, "bounded")

        self.assertEqual([dynamic], audit.new_debt([missing], [dynamic], deny_worsening=True))
        self.assertEqual([missing], audit.new_debt([dynamic], [missing], deny_worsening=True))
        self.assertEqual([invalid_negative], audit.new_debt(
            [invalid_zero], [invalid_negative], deny_worsening=True
        ))
        self.assertEqual([], audit.new_debt([missing], [missing], deny_worsening=True))
        self.assertEqual([], audit.new_debt([invalid_zero], [fixed], deny_worsening=True))
        self.assertEqual([], audit.new_debt([missing], [dynamic]))

    def test_legacy_unknown_runner_timeout_cannot_increase_or_disappear(self):
        path = ".github/workflows/private.yml"
        for status in ("dynamic_runner_unknown", "runner_target_unknown"):
            before = [audit.TimeoutFinding(path, "private", 10, status)]
            worse = [audit.TimeoutFinding(path, "private", 20, status)]
            missing = [audit.TimeoutFinding(path, "private", None, status)]
            improved = [audit.TimeoutFinding(path, "private", 5, status)]
            self.assertEqual(worse, audit.new_debt(before, worse, deny_worsening=True))
            self.assertEqual(missing, audit.new_debt(before, missing, deny_worsening=True))
            self.assertEqual([], audit.new_debt(before, improved, deny_worsening=True))
            self.assertEqual([], audit.new_debt(before, improved))
            self.assertEqual([], audit.new_debt(
                [audit.TimeoutFinding(path, "private", None, status)],
                [audit.TimeoutFinding(path, "private", 10, status)],
                deny_worsening=True,
            ))

    def test_custom_label_timeout_debt_is_rejected_by_strict_new_debt_gate(self):
        path = ".github/workflows/admitted.yml"
        baseline = [audit.TimeoutFinding(path, "old", 10, "bounded")]
        candidate = [
            audit.TimeoutFinding(path, "old", 10, "bounded"),
            audit.TimeoutFinding(path, "new", 3, "runner_target_unknown"),
        ]
        self.assertEqual(candidate[1:], audit.new_debt(baseline, candidate, deny_worsening=True))

    def test_yaml_block_scalar_runner_expressions_are_not_ignored(self):
        source = """jobs:
  folded:
    runs-on: >-
      ${{ matrix.runner }}
    timeout-minutes: 10
  literal:
    runs-on: |
      self-hosted
      zcloud
    timeout-minutes: 15
  hosted:
    runs-on: ubuntu-latest
    timeout-minutes: 50
"""
        rows = audit.inspect_workflow(".github/workflows/scalars.yml", source, max_minutes=30)
        self.assertEqual(["folded", "literal"], [row.job for row in rows])
        self.assertEqual(["dynamic_runner_unknown", "bounded"], [row.status for row in rows])

    def test_bounded_self_hosted_label_is_still_checked(self):
        source = """jobs:
  real:
    runs-on: [self-hosted, zcloud, vps]
    timeout-minutes: 10
  different:
    runs-on: not-self-hosted
    timeout-minutes: 300
"""
        rows = audit.inspect_workflow(".github/workflows/labels.yml", source, max_minutes=30)
        self.assertEqual([("real", "bounded"), ("different", "runner_target_unknown")], [(r.job, r.status) for r in rows])

    def test_legacy_excessive_timeout_cannot_get_longer_or_disappear(self):
        path = ".github/workflows/old.yml"
        base = [audit.TimeoutFinding(path, "slow", 35, "over_limit")]
        worsened = [audit.TimeoutFinding(path, "slow", 60, "over_limit")]
        missing = [audit.TimeoutFinding(path, "slow", None, "missing")]
        improved = [audit.TimeoutFinding(path, "slow", 31, "over_limit")]
        fixed = [audit.TimeoutFinding(path, "slow", 30, "bounded")]
        self.assertEqual(worsened, audit.new_debt(base, worsened, deny_worsening=True))
        self.assertEqual(missing, audit.new_debt(base, missing, deny_worsening=True))
        self.assertEqual([], audit.new_debt(base, improved, deny_worsening=True))
        self.assertEqual([], audit.new_debt(base, fixed, deny_worsening=True))

    def test_new_dynamic_runner_creates_policy_debt(self):
        row = audit.TimeoutFinding(".github/workflows/new.yml", "probe", 5, "dynamic_runner_unknown")
        self.assertEqual([row], audit.new_debt([], [row]))

    def test_strict_cli_rejects_worsened_legacy_timeout_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for key, value in (("user.email", "audit@example.invalid"), ("user.name", "Audit Test")):
                subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
            wf = root / ".github" / "workflows" / "old.yml"
            wf.write_text(
                "jobs:\n  legacy:\n    runs-on: self-hosted\n    timeout-minutes: 35\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            wf.write_text(
                "jobs:\n  legacy:\n    runs-on: self-hosted\n    timeout-minutes: 60\n",
                encoding="utf-8",
            )
            before = wf.read_bytes()
            args = [
                sys.executable, str(Path(audit.__file__).resolve()),
                "--repo-root", str(root), "--base-ref", sha,
            ]
            ordinary = subprocess.run(args, capture_output=True, text=True, check=False)
            strict = subprocess.run(args + ["--require-no-new-debt"], capture_output=True, text=True, check=False)
            self.assertEqual(0, ordinary.returncode, ordinary.stderr)
            self.assertEqual(0, json.loads(ordinary.stdout)["new_debt_count"])
            self.assertEqual(1, strict.returncode, strict.stderr)
            self.assertEqual(1, json.loads(strict.stdout)["new_debt_count"])
            self.assertEqual(before, wf.read_bytes())

    def test_existing_missing_timeout_can_be_fixed(self):
        base = [audit.TimeoutFinding("x.yml", "x", None, "missing")]
        head = [audit.TimeoutFinding("x.yml", "x", 10, "bounded")]
        self.assertEqual([], audit.new_debt(base, head))


if __name__ == "__main__":
    unittest.main()
