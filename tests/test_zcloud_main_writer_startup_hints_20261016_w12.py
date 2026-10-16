"""Real-Git and source-bounded tests for writer startup/runner hints."""
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
import zcloud_main_writer_startup_hints_20261016_w12 as hints


@unittest.skipUnless(shutil.which("git"), "git required")
class WriterStartupHintTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.repo = Path(d.name)
        self.git("init", "-qb", "main")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI")
        self.write("candidate.yml", (
            "name: candidate\non:\n  push:\n    branches: [main]\n"
            "  workflow_dispatch:\npermissions:\n  contents: write\n"
            "jobs:\n  job:\n    runs-on: [self-hosted, zcloud, vps]\n"))
        self.commit()
        self.sha = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              check=True, text=True, capture_output=True).stdout.strip()

    def write(self, name, content):
        path = self.repo / ".github" / "workflows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def capture(self, fmt="json", sha=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = hints.main(["--repo", str(self.repo), "--expected-main-sha",
                               self.sha if sha is None else sha, "--format", fmt])
        return code, buf.getvalue()

    def test_real_main_trigger_context_no_approval(self):
        receipt = hints.audit(self.repo, self.sha)
        self.assertEqual(receipt["candidate_count"], 1)
        self.assertEqual(receipt["workflows_scanned"], 1)
        self.assertEqual(receipt["automatic_event_hint_count"], 1)
        self.assertEqual(receipt["manual_event_hint_count"], 1)
        self.assertEqual(receipt["self_hosted_hint_count"], 1)
        row = receipt["candidates"][0]
        self.assertEqual(row["automatic_event_hints"], ["push"])
        self.assertEqual(row["manual_event_hints"], ["workflow_dispatch"])
        self.assertEqual(row["runner_hints"], ["self_hosted_hint"])
        self.assertEqual(len(row["source_sha256"]), 64)
        self.assertIs(receipt["safe_to_enable_branch_protection"], False)
        self.assertIs(receipt["production_mutation_authorized"], False)
        self.assertIs(receipt["manual_only_verified"], False)

    def test_manual_only_hint_never_certifies_manual_exclusivity(self):
        result = hints.observe("on:\n  workflow_dispatch:\n")
        self.assertEqual(result["manual_event_hints"], ["workflow_dispatch"])
        self.assertEqual(result["automatic_event_hints"], [])
        self.assertFalse(result["manual_only_verified"])
        self.assertFalse(result["actual_activity_verified"])

    def test_auto_schedule_detected(self):
        r = hints.observe("on:\n  schedule:\n    - cron: '17 * * * *'\n"
                          "  workflow_dispatch:\n")
        self.assertEqual(r["automatic_event_hints"], ["schedule"])
        self.assertEqual(r["manual_event_hints"], ["workflow_dispatch"])

    def test_push_path_and_branch_filter_keys_are_only_hints(self):
        result = hints.observe(
            "on:\n  push:\n    branches: [main]\n"
            "    paths:\n      - .github/workflows/example.yml\n"
            "  workflow_dispatch:\n"
        )
        self.assertTrue(result["push_paths_filter_key_hint"])
        self.assertTrue(result["push_branches_filter_key_hint"])
        self.assertIs(result["push_filter_effectiveness_verified"], False)
        self.assertIs(result["manual_only_verified"], False)

    def test_push_without_filter_keys_does_not_invent_filter(self):
        result = hints.observe("on:\n  push:\n")
        self.assertFalse(result["push_paths_filter_key_hint"])
        self.assertFalse(result["push_branches_filter_key_hint"])

    def test_other_event_path_filters_do_not_count_as_push(self):
        result = hints.observe("on:\n  pull_request:\n    paths:\n      - scripts/**\n")
        self.assertFalse(result["push_paths_filter_key_hint"])
        self.assertFalse(result["push_branches_filter_key_hint"])

    def test_paths_key_outside_on_root_not_a_push_filter(self):
        result = hints.observe("on: push\njobs:\n  x:\n    paths:\n      - scripts/**\n")
        self.assertFalse(result["push_paths_filter_key_hint"])

    def test_schedule_never_gets_push_filter_by_accident(self):
        result = hints.observe("on:\n  schedule:\n    - cron: '0 8 * * *'\n")
        self.assertEqual(result["automatic_event_hints"], ["schedule"])
        self.assertFalse(result["push_paths_filter_key_hint"])

    def test_push_scope_evidence_counts_real_git_workflow(self):
        receipt = hints.audit(self.repo, self.sha)
        row = receipt["candidates"][0]
        self.assertTrue(row["push_branches_filter_key_hint"])
        self.assertFalse(row["push_paths_filter_key_hint"])
        self.assertEqual(receipt["push_branches_key_hint_count"], 1)
        self.assertEqual(receipt["push_paths_key_hint_count"], 0)
        self.assertFalse(receipt["safe_to_enable_branch_protection"])

    def test_own_workflow_path_literal_hint_from_exact_yaml(self):
        content = ("on:\n  push:\n    branches: [main]\n    paths:\n"
                   "      - '.github/workflows/proof.yml'\n")
        r = hints.observe(content, workflow_path=".github/workflows/proof.yml")
        self.assertTrue(r["push_paths_filter_key_hint"])
        self.assertTrue(r["push_paths_mentions_own_yaml_literal_hint"])
        self.assertFalse(r["push_filter_effectiveness_verified"])

    def test_other_workflow_path_does_not_count_as_own(self):
        content = ("on:\n  push:\n    paths:\n"
                   "      - '.github/workflows/different.yml'\n")
        r = hints.observe(content, workflow_path=".github/workflows/proof.yml")
        self.assertFalse(r["push_paths_mentions_own_yaml_literal_hint"])

    def test_comment_only_does_not_match_own_path(self):
        content = ("on:\n  push:\n    paths:\n"
                   "      # - '.github/workflows/proof.yml'\n"
                   "      - 'docs/**'\n")
        r = hints.observe(content, workflow_path=".github/workflows/proof.yml")
        self.assertFalse(r["push_paths_mentions_own_yaml_literal_hint"])

    def test_self_path_outside_on_push_is_not_misclassified(self):
        content = ("on:\n  pull_request:\n    paths:\n"
                   "      - '.github/workflows/proof.yml'\n")
        r = hints.observe(content, workflow_path=".github/workflows/proof.yml")
        self.assertFalse(r["push_paths_mentions_own_yaml_literal_hint"])

    def test_source_own_path_hint_not_authority(self):
        p = ".github/workflows/candidate.yml"
        target = self.repo / p
        target.write_text(
            "on:\n  push:\n    branches: [main]\n    paths:\n"
            "      - '.github/workflows/candidate.yml'\n"
            "permissions:\n  contents: write\n",
            encoding="utf-8")
        self.commit()
        result = hints.audit(self.repo, self.git("rev-parse", "HEAD"))
        self.assertEqual(result["push_own_yaml_literal_hint_count"], 1)
        self.assertFalse(result["safe_to_enable_branch_protection"])
        self.assertFalse(result["production_mutation_authorized"])

    def test_inline_yaml_trigger_collection(self):
        r = hints.observe("on: [push, pull_request, workflow_dispatch]\n")
        self.assertEqual(r["automatic_event_hints"], ["pull_request", "push"])
        self.assertEqual(r["manual_event_hints"], ["workflow_dispatch"])
        self.assertFalse(r["unknown_trigger_syntax"])

    def test_scalar_manual_trigger(self):
        r = hints.observe("on: workflow_dispatch\n")
        self.assertEqual(r["manual_event_hints"], ["workflow_dispatch"])

    def test_top_level_comment_excluded(self):
        r = hints.observe("# on: push\non:\n  workflow_dispatch:\n")
        self.assertEqual(r["automatic_event_hints"], [])

    def test_nested_on_not_treated_as_root(self):
        r = hints.observe("jobs:\n  test:\n    env:\n      on: push\n")
        self.assertTrue(r["unknown_trigger_syntax"])
        self.assertEqual(r["automatic_event_hints"], [])

    def test_quoted_root_is_unknown_not_inactive(self):
        r = hints.observe("'on':\n  push:\n")
        self.assertTrue(r["unknown_trigger_syntax"])
        self.assertFalse(r["manual_only_verified"])

    def test_alias_root_is_unknown(self):
        r = hints.observe("on: *inherited_events\n")
        self.assertTrue(r["unknown_trigger_syntax"])

    def test_missing_root_unknown(self):
        r = hints.observe("name: example\njobs: {}\n")
        self.assertTrue(r["unknown_trigger_syntax"])

    def test_duplicate_root_unknown(self):
        r = hints.observe("on: push\non: workflow_dispatch\n")
        self.assertTrue(r["unknown_trigger_syntax"])

    def test_unrecognized_trigger_retained_as_hint(self):
        r = hints.observe("on:\n  future_trigger_type:\n")
        self.assertEqual(r["unrecognized_event_hints"], ["future_trigger_type"])
        self.assertFalse(r["actual_activity_verified"])

    def test_hosted_runner_hint(self):
        r = hints.observe("on: push\njobs:\n  x:\n    runs-on: ubuntu-latest\n")
        self.assertEqual(r["runner_hints"], ["github_hosted_hint"])

    def test_dynamic_runner_is_unknown(self):
        r = hints.observe("on: push\njobs:\n  x:\n    runs-on: " + "$" +
                          "{{ matrix.os }}\n")
        self.assertIn("dynamic_or_multiline_runner_hint", r["runner_hints"])

    def test_missing_runner_does_not_certify_safe(self):
        r = hints.observe("on: push\n")
        self.assertEqual(r["runner_hints"], [])
        self.assertFalse(r["actual_activity_verified"])

    def test_csv_rows_are_sha_bound_and_never_authorizing(self):
        status, output = self.capture("csv")
        self.assertEqual(status, 0)
        rows = list(csv.DictReader(io.StringIO(output)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["main_sha"], self.sha)
        self.assertEqual(len(rows[0]["source_sha256"]), 64)
        self.assertEqual(rows[0]["automatic_event_hints"], "push")
        self.assertEqual(rows[0]["manual_only_verified"], "false")
        self.assertEqual(rows[0]["branch_protection_authorized"], "false")

    def test_markdown_is_human_readable_not_raw_source(self):
        status, output = self.capture("markdown")
        self.assertEqual(status, 0)
        self.assertIn("GEEN VRIJGAVE", output)
        self.assertIn("candidate_count: 1", output)
        self.assertNotIn("permissions:\n  contents: write", output)

    def test_invalid_main_sha_denied(self):
        status, out = self.capture(sha="wrong")
        self.assertEqual(status, 2)
        data = json.loads(out)
        self.assertEqual(data["error"], "invalid_main_sha")
        self.assertFalse(data["production_mutation_authorized"])

    def test_moving_main_denied(self):
        self.write("new.yml", "on: push\npermissions:\n  contents: write\n")
        self.commit()
        with self.assertRaisesRegex(hints.core.InventoryError, "main_sha_drift"):
            hints.audit(self.repo, self.sha)

    def test_changed_digest_denied_after_immutable_read(self):
        from zcloud_main_writer_compat_inventory_20261016_w12 import inventory
        row = inventory(self.repo, self.sha)["candidates"][0].copy()
        row["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(hints.Denied, "workflow_sha256_drift"):
            hints.read_blob(self.repo, self.sha, row)

    def test_symlink_workflow_denied(self):
        (self.repo / ".github" / "workflows" / "alias.yml").symlink_to("candidate.yml")
        self.commit()
        with self.assertRaisesRegex(hints.core.InventoryError, "unsafe_workflow_tree"):
            hints.audit(self.repo, self.git("rev-parse", "HEAD"))

    def test_hidden_yaml_control_denied(self):
        self.write("candidate.yml", "name: malicious\x0bhidden\non: push\n"
                   "permissions:\n  contents: write\n")
        self.commit()
        with self.assertRaisesRegex(hints.core.InventoryError, "invalid_workflow_controls"):
            hints.audit(self.repo, self.git("rev-parse", "HEAD"))

    def test_untrusted_caller_git_dir_ignored(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/fake",
                                                      "GIT_CONFIG_COUNT": "1",
                                                      "GIT_CONFIG_KEY_0": "core.bare",
                                                      "GIT_CONFIG_VALUE_0": "true"}):
            self.assertEqual(hints.audit(self.repo, self.sha)["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
