"""Real-Git, no-network adversarial tests for the main-writer offline inventory."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_main_writer_compat_inventory_20261016_w12 as audit


@unittest.skipUnless(shutil.which("git"), "git required")
class MainWriterInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.run_git("init", "-q", "-b", "main")
        self.run_git("config", "user.name", "CI")
        self.run_git("config", "user.email", "ci@example.invalid")
        self.write("safe.yml", "name: safe\non: push\njobs: {}\n")
        self.commit()
        self.sha = self.rev()

    def run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              check=True, text=True, capture_output=True).stdout.strip()

    def rev(self):
        return self.run_git("rev-parse", "HEAD")

    def write(self, name, body):
        dest = self.repo / ".github" / "workflows" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")

    def commit(self):
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", "fixture")

    def capture(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = audit.main(list(args))
        return status, output.getvalue()

    def test_safe_workflow_is_not_proven_safe_for_protection(self):
        result = audit.inventory(self.repo, self.sha)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["workflows_scanned"], 1)
        self.assertFalse(result["safe_to_enable_branch_protection"])
        self.assertFalse(result["historical_activity_verified"])
        self.assertFalse(result["production_mutation_authorized"])

    def test_direct_main_push_has_line_number_and_immutable_digest(self):
        self.write("main.yml", "jobs:\n  proof:\n    steps:\n      - run: git push origin HEAD:main\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        row = next(c for c in result["candidates"] if c["path"].endswith("main.yml"))
        self.assertEqual(row["classification"], "direct_writer_candidate")
        self.assertIn({"line": 4, "kind": "direct_main_ref"}, row["signals"])
        self.assertEqual(len(row["source_sha256"]), 64)
        self.assertEqual(row["disposition"], "manual_review_required")

    def test_api_writer_requires_review(self):
        self.write("api.yaml", "run: gh api -X PATCH repos/acme/x/git/refs/heads/main\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        self.assertEqual(result["direct_writer_candidate_count"], 1)

    def test_other_push_is_ambiguous_not_cleared(self):
        self.write("other.yml", "run: git push origin some-branch\n")
        self.commit()
        rows = audit.inventory(self.repo, self.rev())["candidates"]
        self.assertEqual(rows[0]["classification"], "capability_or_indirect_writer_candidate")

    def test_workflow_contents_write_grant_requires_review(self):
        self.write("permissions.yml", "permissions:\n  contents: write\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["direct_writer_candidate_count"], 0)

    def test_comments_are_not_operational_evidence(self):
        self.write("safe.yml", "# git push origin HEAD:main\nname: safe\n")
        self.commit()
        self.assertEqual(audit.inventory(self.repo, self.rev())["candidate_count"], 0)

    def test_pr_merge_is_writer_candidate(self):
        self.write("merge.yml", "run: gh pr merge --squash --auto\n")
        self.commit()
        self.assertEqual(audit.inventory(self.repo, self.rev())["direct_writer_candidate_count"], 1)

    def test_exact_main_ref_and_head_required(self):
        with self.assertRaisesRegex(audit.InventoryError, "invalid_expected_sha"):
            audit.inventory(self.repo, "abc")
        self.run_git("checkout", "-qb", "topic")
        self.write("branch-only.yml", "name: divergent\\n".replace("\\n","\n"))
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "main_sha_drift"):
            audit.inventory(self.repo, self.sha)

    def test_stale_main_sha_denied(self):
        self.write("new.yml", "name: newer\n")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "main_sha_drift"):
            audit.inventory(self.repo, self.sha)

    def test_symlinked_workflow_denied(self):
        (self.repo / ".github" / "workflows" / "linked.yml").symlink_to("safe.yml")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "unsafe_workflow_tree"):
            audit.inventory(self.repo, self.rev())

    def test_binary_yaml_denied(self):
        (self.repo / ".github" / "workflows" / "bad.yml").write_bytes(b"name: \xff\n")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "invalid_workflow_encoding"):
            audit.inventory(self.repo, self.rev())

    def test_source_not_reflected_in_cli_errors(self):
        status, stdout = self.capture("--repo", str(self.repo),
                                      "--expected-main-sha", "not-a-sha")
        result = json.loads(stdout)
        self.assertEqual(status, 2)
        self.assertFalse(result["production_mutation_authorized"])
        self.assertFalse(result["safe_to_enable_branch_protection"])
        self.assertEqual(result["error"], "invalid_expected_sha")

    def test_markdown_contains_manual_disposition(self):
        self.write("main.yml", "run: git push origin HEAD:main\n")
        self.commit()
        status, output = self.capture("--repo", str(self.repo),
                                      "--expected-main-sha", self.rev(),
                                      "--format", "markdown")
        self.assertEqual(status, 0)
        self.assertIn("GEEN VRIJGAVE", output)
        self.assertIn("migreren / uitfaseren / false positive", output)
        self.assertNotIn("run: git push", output)

    def test_multiple_signals_do_not_overcount_workflows(self):
        self.write("main.yml",
                   "permissions:\n  contents: write\nsteps:\n - run: git push origin HEAD:main\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["direct_writer_candidate_count"], 1)
        self.assertEqual(len(result["candidates"][0]["signals"]), 3)

    def test_json_contract_contains_no_approval_evidence(self):
        status, stdout = self.capture("--repo", str(self.repo),
                                      "--expected-main-sha", self.sha)
        self.assertEqual(status, 0)
        payload = json.loads(stdout)
        self.assertTrue(payload["static_scan_complete"])
        for field in ("production_mutation_authorized", "safe_to_enable_branch_protection",
                      "historical_activity_verified", "github_branch_protection_verified",
                      "pr_branch_ownership_verified"):
            self.assertIs(payload[field], False)

    def test_size_cap_denies_oversized_workflow(self):
        (self.repo / ".github" / "workflows" / "large.yml").write_bytes(
            b"#" * (audit.MAX_BLOB_BYTES + 1))
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "workflow_blob_out_of_bounds"):
            audit.inventory(self.repo, self.rev())

    def test_nul_in_workflow_blob_denied(self):
        (self.repo / ".github" / "workflows" / "nul.yml").write_bytes(b"name: a\x00b\n")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "invalid_workflow_blob"):
            audit.inventory(self.repo, self.rev())

    def test_missing_workflow_tree_denied(self):
        self.run_git("rm", "-r", ".github/workflows")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "missing_workflow_tree"):
            audit.inventory(self.repo, self.rev())

    def test_git_large_output_is_denied_before_capture_exceeds_budget(self):
        shim_dir = self.repo / "shim-git"
        shim_dir.mkdir()
        fake = shim_dir / "git"
        fake.write_text("#!/bin/sh\nhead -c 10485760 /dev/zero\n", encoding="utf-8")
        fake.chmod(0o755)
        # This fake Git emits 10 MiB; the helper must reject at 129 bytes
        # without storing the entire attacker-controlled output in memory.
        with mock.patch.dict(os.environ, {"PATH": str(shim_dir) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(audit.InventoryError, "git_output_too_large"):
                audit.git(self.repo, "ls-tree", "unused", max_output=128)

    def test_git_nonzero_child_exit_denied(self):
        shim_dir = self.repo / "shim-failure"
        shim_dir.mkdir()
        fake = shim_dir / "git"
        fake.write_text("#!/bin/sh\nprintf 'not a trusted result'\nexit 9\n", encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(shim_dir) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(audit.InventoryError, "git_read_failed"):
                audit.git(self.repo, "ls-tree", "unused", max_output=128)

    def test_git_streaming_keeps_exact_binary_bytes(self):
        output = audit.git(self.repo, "ls-tree", "-rz", "--full-tree",
                           self.sha, ".github/workflows", max_output=4096)
        self.assertIn(b"\x00", output)
        self.assertIn(b".github/workflows/safe.yml", output)

    def test_csv_source_provenance_and_review_fields_remain_empty(self):
        self.write("writer.yml", "run: git push origin HEAD:main\n")
        self.commit()
        status, stdout = self.capture("--repo", str(self.repo),
                                      "--expected-main-sha", self.rev(),
                                      "--format", "csv")
        self.assertEqual(status, 0)
        rows = list(csv.DictReader(io.StringIO(stdout)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["main_sha"], self.rev())
        self.assertEqual(rows[0]["workflow_path"],
                         ".github/workflows/writer.yml")
        self.assertEqual(len(rows[0]["source_sha256"]), 64)
        self.assertEqual(rows[0]["reviewed_by"], "")
        self.assertEqual(rows[0]["decision"], "")
        self.assertEqual(rows[0]["review_verified"], "false")
        self.assertEqual(rows[0]["branch_protection_authorized"], "false")
        self.assertIn("direct_main_ref", rows[0]["signal_lines_and_kinds"])

    def test_csv_no_writer_does_not_claim_review_or_approval(self):
        status, stdout = self.capture("--repo", str(self.repo),
                                      "--expected-main-sha", self.sha,
                                      "--format", "csv")
        self.assertEqual(status, 0)
        rows = list(csv.reader(io.StringIO(stdout)))
        self.assertEqual(len(rows), 1)
        self.assertIn("source_sha256", rows[0])

    def test_git_config_option_direct_push_is_reviewed(self):
        self.write("option.yml", "run: git -c core.logAllRefUpdates=true push origin HEAD:main\n")
        self.commit()
        row = next(r for r in audit.inventory(self.repo, self.rev())["candidates"]
                   if r["path"].endswith("option.yml"))
        self.assertEqual(row["classification"], "direct_writer_candidate")
        self.assertIn("direct_main_ref", {signal["kind"] for signal in row["signals"]})

    def test_git_worktree_argument_direct_push_is_reviewed(self):
        self.write("worktree.yml", "run: git -C /tmp/repo push --force origin HEAD:main\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        self.assertEqual(result["direct_writer_candidate_count"], 1)

    def test_git_gitdir_equals_argument_direct_push_is_reviewed(self):
        self.write("gitdir.yml", "run: git --git-dir=/opt/src/.git push origin refs/heads/main\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        self.assertEqual(result["direct_writer_candidate_count"], 1)

    def test_multiline_git_push_backslash_is_strong_candidate(self):
        self.write("continued.yml", "jobs:\n  build:\n    steps:\n"
                   "      - run: |\n          git \\\n            push origin HEAD:main\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        row = next(r for r in result["candidates"] if r["path"].endswith("continued.yml"))
        self.assertEqual(row["classification"], "direct_writer_candidate")
        self.assertIn({"line": 5, "kind": "direct_main_ref"}, row["signals"])

    def test_multiline_gh_pr_merge_backslash_is_strong_candidate(self):
        self.write("continued-merge.yml", "run: |\n  gh pr \\\n    merge --squash 42\n")
        self.commit()
        row = next(r for r in audit.inventory(self.repo, self.rev())["candidates"]
                   if r["path"].endswith("continued-merge.yml"))
        self.assertIn({"line": 2, "kind": "pull_request_merge"}, row["signals"])

    def test_backslash_comment_does_not_create_fake_command(self):
        self.write("comment.yml", "run: |\n  # git \\\n  push origin HEAD:main\n")
        self.commit()
        self.assertEqual(audit.inventory(self.repo, self.rev())["candidate_count"], 0)

    def test_git_config_and_dir_override_from_environment_ignored(self):
        forged = self.repo / "bogus.git"
        with mock.patch.dict(os.environ, {
            "GIT_DIR": str(forged),
            "GIT_WORK_TREE": "/ignored",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
            "GIT_CONFIG_VALUE_0": "999",
        }):
            result = audit.inventory(self.repo, self.sha)
        self.assertEqual(result["workflows_scanned"], 1)
        self.assertEqual(result["main_sha"], self.sha)
        self.assertFalse(result["safe_to_enable_branch_protection"])

    def test_multiline_safe_source_stays_unreviewed(self):
        self.write("long.yml", "run: |\n  printf 'hello' \\\n      'world'\n")
        self.commit()
        result = audit.inventory(self.repo, self.rev())
        self.assertEqual(result["candidate_count"], 0)

    def test_vertical_tab_cannot_hide_github_writer(self):
        self.write("hidden.yml", "name: testing\u000bgit push origin HEAD:main\n")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "invalid_workflow_controls"):
            audit.inventory(self.repo, self.rev())

    def test_hidden_del_character_rejected(self):
        self.write("hidden.yml", "name: testing\x7fgit push origin HEAD:main\n")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "invalid_workflow_controls"):
            audit.inventory(self.repo, self.rev())

    def test_crlf_workflow_line_endings_supported(self):
        target = self.repo / ".github" / "workflows" / "crlf.yml"
        target.write_bytes(b"name: crlf\r\njobs:\r\n  x:\r\n    steps:\r\n"
                           b"      - run: git push origin HEAD:main\r\n")
        self.commit()
        row = next(row for row in audit.inventory(self.repo, self.rev())["candidates"]
                   if row["path"].endswith("crlf.yml"))
        self.assertIn({"line": 5, "kind": "direct_main_ref"}, row["signals"])

    def test_no_git_config_or_working_tree_trust(self):
        # Inspect committed blob only; working-tree change without commit is irrelevant.
        self.write("safe.yml", "run: git push origin HEAD:main\n")
        result = audit.inventory(self.repo, self.sha)
        self.assertEqual(result["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
