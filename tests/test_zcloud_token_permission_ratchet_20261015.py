"""Real-Git adversarial regression suite for token-permission drift."""
from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import subprocess
import tempfile
import unittest

from scripts.zcloud_token_permission_ratchet_20261015 import (
    IncompleteSource, check, format_ci_summary, main, permission_debt,
)


class PermissionRatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="token-ratchet-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Source Test")
        self.git("config", "user.email", "source@example.invalid")
        self.put(".github/workflows/original.yml",
                 "name: original\non: push\npermissions:\n  contents: read\n"
                 "jobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.base = self.commit("baseline")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.root, stderr=subprocess.DEVNULL
        ).decode().strip()

    def put(self, name, contents):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    def commit(self, label):
        self.git("add", "-A")
        self.git("commit", "-qm", label)
        return self.git("rev-parse", "HEAD")

    def check_file(self, text, name=".github/workflows/original.yml"):
        self.put(name, text)
        return check(self.root, self.base, self.commit("candidate"))

    def test_read_only_source_passes(self):
        result = self.check_file(
            "name: changed\non: push\npermissions:\n  contents: read\n"
            "jobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    permissions:\n      contents: read\n"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_risks"], 0)

    def test_top_level_write_all_denied(self):
        value = self.check_file("on: push\npermissions: write-all\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])
        self.assertEqual(value["new_risks"], 3)
        self.assertEqual(value["removed_safe_scopes"], 1)

    def test_job_level_write_denied(self):
        value = self.check_file("on: push\njobs:\n  build:\n    permissions:\n"
                                "      id-token: write\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_new_workflow_write_denied(self):
        value = self.check_file("on: push\npermissions:\n  statuses: write\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
                                ".github/workflows/new.yml")
        self.assertFalse(value["ok"])

    def test_untrusted_expression_denied(self):
        value = self.check_file("on: push\npermissions: ${{ inputs.perms }}\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_flow_mapping_denied(self):
        value = self.check_file("on: push\npermissions: {contents: write}\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_quoted_keys_denied(self):
        value = self.check_file("on: push\n'permissions':\n  'id-token': write\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_read_comment_not_write(self):
        value = self.check_file("on: push\npermissions:\n  contents: read # write\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertTrue(value["ok"])

    def test_grandfather_exact_existing_debt(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: write-all\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        old = self.commit("legacy")
        self.put("README.md", "non workflow change")
        new = self.commit("docs")
        outcome = check(self.root, old, new)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["baseline_risks"], 2)

    def test_move_legacy_risk_to_different_job_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  old:\n    permissions:\n      actions: write\n    runs-on: ubuntu-latest\n")
        old = self.commit("legacy")
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  new:\n    permissions:\n      actions: write\n    runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, old, self.commit("move"))["ok"])

    def test_duplicated_legacy_risk_denied(self):
        self.put(".github/workflows/original.yml", "on: push\npermissions: write-all\njobs: {}\n")
        old = self.commit("legacy")
        self.put(".github/workflows/original.yml", "on: push\npermissions: write-all\npermissions: write-all\njobs: {}\n")
        self.assertFalse(check(self.root, old, self.commit("duplicate"))["ok"])

    def test_opaque_mapping_denied(self):
        value = self.check_file("on: push\npermissions:\n  contents: read\n  checks: ${{ input.checks }}\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_reject_symlink(self):
        target = self.root / "README.md"
        target.write_text("on: push\npermissions: write-all\n")
        workflow = self.root / ".github/workflows/original.yml"
        workflow.unlink()
        workflow.symlink_to(target)
        new = self.commit("symlink")
        with self.assertRaises(IncompleteSource):
            check(self.root, self.base, new)

    def test_reject_backwards_ancestry_and_invalid_sha(self):
        self.put("README.md", "change")
        new = self.commit("change")
        with self.assertRaises(IncompleteSource):
            check(self.root, new, self.base)
        with self.assertRaises(IncompleteSource):
            check(self.root, "invalid", new)

    def test_release_authority_never_granted(self):
        value = self.check_file("on: push\npermissions: write-all\njobs: {}\n")
        for flag in ("release_authorized", "deployment_authorized", "mutation_performed"):
            self.assertIs(value[flag], False)

    def test_secret_like_workflow_comment_not_emitted(self):
        value = permission_debt(
            "permissions: write-all\n# SECRET_TOKEN=private\n",
            ".github/workflows/example.yml"
        )
        self.assertNotIn("SECRET_TOKEN", repr(value))


    def test_whitespace_before_permissions_colon_denied(self):
        value = self.check_file("on: push\npermissions : write-all\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_yaml_explicit_mapping_key_denied(self):
        value = self.check_file("on: push\n? permissions\n: write-all\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_unicode_escaped_permission_key_denied(self):
        value = self.check_file('on: push\n"permiss\\u0069ons": write-all\njobs: {}\n')
        self.assertFalse(value["ok"])

    def test_yaml_merge_alias_denied(self):
        value = self.check_file("on: push\npermissions: &admin write-all\n<<: *admin\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_malformed_quoted_read_is_not_safe(self):
        value = self.check_file("on: push\npermissions:\n  contents: 'read\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_job_level_write_on_compact_jobs_flow_fails_closed(self):
        value = self.check_file("on: push\njobs: { test: { permissions: write-all } }\n")
        self.assertFalse(value["ok"])


    def test_opaque_legacy_mapping_changed_content_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: {contents: read}\njobs: {}\n")
        old = self.commit("legacy-opaque")
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: {contents: write}\njobs: {}\n")
        result = check(self.root, old, self.commit("opaque-write"))
        self.assertFalse(result["ok"])

    def test_compact_legacy_flow_jobs_changed_denied(self):
        self.put(".github/workflows/original.yml", "on: push\njobs: { test: { permissions: read-all } }\n")
        old = self.commit("flow-old")
        self.put(".github/workflows/original.yml", "on: push\njobs: { test: { permissions: write-all } }\n")
        self.assertFalse(check(self.root, old, self.commit("flow-new"))["ok"])

    def test_opaque_legacy_expression_swap_denied(self):
        self.put(".github/workflows/original.yml", "on: push\npermissions: ${{ inputs.a }}\njobs: {}\n")
        old = self.commit("expression-old")
        self.put(".github/workflows/original.yml", "on: push\npermissions: ${{ inputs.b }}\njobs: {}\n")
        self.assertFalse(check(self.root, old, self.commit("expression-new"))["ok"])


    def test_removing_explicit_read_only_repo_policy_denied(self):
        value = self.check_file("on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_removing_safe_job_permissions_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  test:\n    permissions:\n      contents: read\n    runs-on: ubuntu-latest\n")
        old = self.commit("safe-job")
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, old, self.commit("removed-scope"))["ok"])

    def test_allow_safer_read_to_none_with_same_scope(self):
        value = self.check_file(
            "on: push\npermissions:\n  contents: none\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        )
        self.assertTrue(value["ok"])


    def test_new_workflow_without_explicit_token_permissions_denied(self):
        value = self.check_file(
            "on: push\njobs:\n  new:\n    runs-on: ubuntu-latest\n",
            ".github/workflows/unscoped.yml",
        )
        self.assertFalse(value["ok"])

    def test_new_job_in_legacy_implicit_workflow_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  legacy:\n    runs-on: ubuntu-latest\n")
        old = self.commit("legacy-default")
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  legacy:\n    runs-on: ubuntu-latest\n"
                 "  added:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, old, self.commit("add-job"))["ok"])

    def test_explicit_readonly_new_workflow_allowed(self):
        value = self.check_file(
            "on: push\npermissions:\n  contents: read\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n",
            ".github/workflows/read-only.yml",
        )
        self.assertTrue(value["ok"])

    def test_new_job_with_explicit_read_only_in_legacy_allowed(self):
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  legacy:\n    runs-on: ubuntu-latest\n")
        old = self.commit("legacy-default")
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  legacy:\n    runs-on: ubuntu-latest\n"
                 "  new:\n    permissions:\n      contents: read\n    runs-on: ubuntu-latest\n")
        self.assertTrue(check(self.root, old, self.commit("add-safe-job"))["ok"])


    def test_legacy_alias_anchor_mutated_into_write_denied(self):
        old = ("on: push\npermissions: *trust\n"
               "x-permission-set: &trust\n  contents: read\njobs: {}\n")
        new = old.replace("contents: read", "contents: write")
        self.put(".github/workflows/original.yml", old)
        base = self.commit("legacy-alias")
        self.put(".github/workflows/original.yml", new)
        result = check(self.root, base, self.commit("mutated-anchor"))
        self.assertFalse(result["ok"])

    def test_job_alias_anchor_mutated_into_write_denied(self):
        old = ("on: push\nx-permission-set: &trust\n  actions: read\n"
               "jobs:\n  test:\n    permissions: *trust\n    runs-on: ubuntu-latest\n")
        self.put(".github/workflows/original.yml", old)
        base = self.commit("legacy-job-alias")
        self.put(".github/workflows/original.yml", old.replace("actions: read", "actions: write"))
        self.assertFalse(check(self.root, base, self.commit("mutable-job-alias"))["ok"])

    def test_unrelated_other_file_allowed_with_legacy_alias(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: *trust\n"
                 "x-permission-set: &trust\n  contents: read\njobs: {}\n")
        base = self.commit("legacy-unmodified")
        self.put("README.md", "no changes to YAML\n")
        self.assertTrue(check(self.root, base, self.commit("docs-only"))["ok"])

    def test_legacy_alias_changed_comment_fails_closed(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: *trust\n"
                 "x-permission-set: &trust\n  contents: read\njobs: {}\n")
        base = self.commit("legacy-anchor")
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: *trust\n"
                 "x-permission-set: &trust\n  contents: read\n"
                 "# candidate must prove unchanged opaque YAML\njobs: {}\n")
        self.assertFalse(check(self.root, base, self.commit("comment-change"))["ok"])


    def test_inline_job_flow_write_overrides_safe_root_denied(self):
        result = self.check_file(
            "on: push\npermissions:\n  contents: read\njobs:\n"
            "  test: {permissions: {contents: write}, runs-on: ubuntu-latest}\n"
        )
        self.assertFalse(result["ok"])

    def test_legacy_normal_job_replaced_with_inline_job_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions:\n  contents: read\njobs:\n"
                 "  test:\n    runs-on: ubuntu-latest\n")
        base = self.commit("safe-ordinary-job")
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions:\n  contents: read\njobs:\n"
                 "  test: {permissions: write-all, runs-on: ubuntu-latest}\n")
        self.assertFalse(check(self.root, base, self.commit("inline-write-job"))["ok"])

    def test_alias_job_can_override_safe_root_denied(self):
        value = self.check_file(
            "on: push\npermissions:\n  contents: read\n"
            "x-job: &writer\n  permissions: write-all\njobs:\n  test: *writer\n"
        )
        self.assertFalse(value["ok"])

    def test_unchanged_legacy_opaque_job_with_docs_only_allowed(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions:\n  contents: read\n"
                 "jobs:\n  test: {runs-on: ubuntu-latest}\n")
        base = self.commit("legacy-inline-job")
        self.put("README.md", "harmless")
        self.assertTrue(check(self.root, base, self.commit("harmless-docs"))["ok"])


    def test_reject_nonutf8_workflow_git_blob(self):
        path = self.root / ".github/workflows/original.yml"
        path.write_bytes(b"on: push\npermissions: \xff\njobs: {}\n")
        head = self.commit("non-utf8")
        with self.assertRaises(IncompleteSource):
            check(self.root, self.base, head)

    def test_reject_oversized_workflow_blob(self):
        self.put(".github/workflows/original.yml", "on: push\n" + ("x" * (1024 * 1024 + 1)))
        head = self.commit("oversized-blob")
        with self.assertRaises(IncompleteSource):
            check(self.root, self.base, head)

    def test_reject_gitlink_beneath_actions_workflows(self):
        self.git("update-index", "--add", "--cacheinfo",
                 f"160000,{self.base},.github/workflows/vendor")
        # Commit the staged gitlink directly: git add -A would otherwise
        # discard this synthetic gitlink because no worktree directory exists.
        self.git("commit", "-qm", "nested-gitlink")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaises(IncompleteSource):
            check(self.root, self.base, head)

    def test_cli_rejects_invalid_source_without_leaking_paths(self):
        output = StringIO()
        with redirect_stdout(output):
            result = main(["--root", str(self.root), "--base", "not-a-sha", "--head", self.base])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "incomplete_git_source")
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["deployment_authorized"])
        self.assertFalse(report["mutation_performed"])
        self.assertNotIn(str(self.root), output.getvalue())


    def test_permission_like_text_in_literal_shell_step_is_not_an_api_scope(self):
        result = self.check_file(
            "on: push\npermissions:\n  contents: read\njobs:\n  test:\n"
            "    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
            "          printf 'example'\n          permissions: write-all\n"
        )
        self.assertTrue(result["ok"])

    def test_permission_like_text_in_folded_shell_step_is_not_an_api_scope(self):
        result = self.check_file(
            "on: push\npermissions:\n  contents: read\njobs:\n  test:\n"
            "    runs-on: ubuntu-latest\n    steps:\n      - run: >-\n"
            "          echo permissions: write-all\n"
            "          permissions: write-all\n"
        )
        self.assertTrue(result["ok"])

    def test_real_job_write_after_literal_shell_step_denied(self):
        result = self.check_file(
            "on: push\npermissions:\n  contents: read\njobs:\n  test:\n"
            "    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
            "          permissions: write-all\n"
            "    permissions: write-all\n"
        )
        self.assertFalse(result["ok"])

    def test_malformed_non_run_scalar_does_not_hide_new_write(self):
        result = self.check_file(
            "on: push\npermissions:\n  contents: read\njobs:\n  test:\n"
            "    runs-on: ubuntu-latest\n    config: |\n"
            "      permissions: write-all\n"
        )
        self.assertFalse(result["ok"])


    def test_human_summary_good_has_bounded_counts_and_no_authority(self):
        payload = dict(policy="zcloud-token-permission-no-new-debt-v1",
                       baseline_risks=119, candidate_risks=119, new_risks=0,
                       removed_safe_scopes=0, ok=True,
                       release_authorized=False, deployment_authorized=False,
                       mutation_performed=False)
        summary = format_ci_summary(payload)
        self.assertIn("119", summary)
        self.assertIn("Nieuwe risico", summary)
        self.assertIn("Geen productievrijgave", summary)
        self.assertNotIn("release_authorized=true", summary)

    def test_human_summary_red_report_is_fail_closed_and_redacted(self):
        payload = dict(policy="zcloud-token-permission-no-new-debt-v1",
                       ok=False, reason="SECRET=unexpected",
                       release_authorized=False, deployment_authorized=False,
                       mutation_performed=False)
        summary = format_ci_summary(payload)
        self.assertIn("Afgewezen", summary)
        self.assertNotIn("SECRET", summary)
        self.assertIn("Geen productievrijgave", summary)

    def test_human_summary_rejects_forged_mutation_permission(self):
        payload = dict(policy="zcloud-token-permission-no-new-debt-v1",
                       ok=False, release_authorized=True,
                       deployment_authorized=False, mutation_performed=False)
        with self.assertRaises(IncompleteSource):
            format_ci_summary(payload)

    def test_human_summary_rejects_noninteger_or_contradictory_counts(self):
        source = dict(policy="zcloud-token-permission-no-new-debt-v1",
                      baseline_risks=119, candidate_risks=119,
                      new_risks=0, removed_safe_scopes=0, ok=True,
                      release_authorized=False, deployment_authorized=False,
                      mutation_performed=False)
        for update in ({"new_risks": True}, {"new_risks": -1},
                       {"candidate_risks": 1_000_000_000},
                       {"new_risks": 1}, {"removed_safe_scopes": 1}):
            payload = dict(source, **update)
            with self.subTest(update=update), self.assertRaises(IncompleteSource):
                format_ci_summary(payload)


    def test_real_cli_emits_successful_dutch_summary_without_vps_authority(self):
        self.put("README.md", "source-only test")
        head = self.commit("safe-candidate")
        target = self.root / "summary.md"
        output = StringIO()
        with redirect_stdout(output):
            rc = main(["--root", str(self.root), "--base", self.base,
                       "--head", head, "--summary-to", str(target)])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        text = target.read_text(encoding="utf-8")
        self.assertIn("Broncontrole geslaagd", text)
        self.assertIn("Geen productievrijgave", text)

    def test_real_cli_emits_redacted_denial_summary(self):
        target = self.root / "summary.md"
        output = StringIO()
        with redirect_stdout(output):
            rc = main(["--root", str(self.root), "--base", "not-a-sha",
                       "--head", self.base, "--summary-to", str(target)])
        self.assertEqual(rc, 1)
        self.assertIn("Afgewezen", target.read_text(encoding="utf-8"))
        self.assertNotIn(str(self.root), target.read_text(encoding="utf-8"))

    def test_real_cli_summary_write_failure_denies_even_safe_source(self):
        self.put("README.md", "safe candidate")
        head = self.commit("safe")
        output = StringIO()
        with redirect_stdout(output):
            rc = main(["--root", str(self.root), "--base", self.base,
                       "--head", head, "--summary-to", str(self.root)])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(output.getvalue())["reason"],
                         "summary_evidence_failed")


    def test_quoted_jobs_key_new_inherited_job_denied(self):
        source = ("on: push\n'jobs':\n  legacy:\n"
                  "    runs-on: ubuntu-latest\n")
        self.put(".github/workflows/original.yml", source)
        before = self.commit("opaque-quoted-jobs")
        self.put(".github/workflows/original.yml", source +
                 "  new_job:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, before, self.commit("added-implicit-job"))["ok"])

    def test_jobs_alias_anchor_expansion_denied(self):
        source = ("on: push\nx-jobs: &collection\n  legacy:\n"
                  "    runs-on: ubuntu-latest\njobs: *collection\n")
        self.put(".github/workflows/original.yml", source)
        before = self.commit("jobs-alias")
        self.put(".github/workflows/original.yml", source.replace(
            "jobs: *collection", "jobs: *collection").replace(
            "jobs: *collection", "jobs: *collection\nx-extra: should-not-be-inherited\n"))
        self.assertFalse(check(self.root, before, self.commit("anchor-changed"))["ok"])

    def test_nonstandard_four_space_jobs_new_implicit_job_denied(self):
        old = ("on: push\njobs:\n    old_job:\n"
               "        runs-on: ubuntu-latest\n")
        self.put(".github/workflows/original.yml", old)
        before = self.commit("legacy-nonstandard-indent")
        self.put(".github/workflows/original.yml", old +
                 "    new_job:\n        runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, before, self.commit("new-wide-job"))["ok"])

    def test_ambiguous_legacy_jobs_structure_unchanged_docs_only_allowed(self):
        self.put(".github/workflows/original.yml",
                 "on: push\n'jobs':\n  legacy:\n    runs-on: ubuntu-latest\n")
        before = self.commit("legacy-ambiguous-jobs")
        self.put("README.md", "unrelated docs edit\n")
        self.assertTrue(check(self.root, before, self.commit("docs-only"))["ok"])

    def test_rewritten_jobs_alias_mapping_target_denied(self):
        old = ("on: push\nx-jobs: &group\n  legacy:\n"
               "    runs-on: ubuntu-latest\njobs: *group\n")
        self.put(".github/workflows/original.yml", old)
        before = self.commit("legacy-flow-jobs")
        self.put(".github/workflows/original.yml", old.replace(
            "runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n"
            "    permissions: write-all"))
        self.assertFalse(check(self.root, before, self.commit("alias-target-write"))["ok"])


if __name__ == "__main__":
    unittest.main()
