"""Real-Git, no-network adversarial tests for the main-writer offline inventory."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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
        (self.repo / ".github" / "workflows" / "nul.yml").write_bytes(b"name: a\\x00b\\n".replace(b"\\x00", b"\\x00"))
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "invalid_workflow_blob"):
            audit.inventory(self.repo, self.rev())

    def test_missing_workflow_tree_denied(self):
        self.run_git("rm", "-r", ".github/workflows")
        self.commit()
        with self.assertRaisesRegex(audit.InventoryError, "missing_workflow_tree"):
            audit.inventory(self.repo, self.rev())

    def test_no_git_config_or_working_tree_trust(self):
        # Inspect committed blob only; working-tree change without commit is irrelevant.
        self.write("safe.yml", "run: git push origin HEAD:main\n")
        result = audit.inventory(self.repo, self.sha)
        self.assertEqual(result["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
