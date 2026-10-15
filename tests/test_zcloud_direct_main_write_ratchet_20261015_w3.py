"""Real Git adversarial source checks; no live repository mutation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest import mock
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_direct_main_write_ratchet_20261015_w3.py"
SAFE = "name: safe\non: pull_request\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n"


class RatchetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI Fixture")
        self.git("config", "user.email", "ci@example.invalid")
        self.write(".github/workflows/safe.yml", SAFE)
        self.base = self.commit()

    def git(self, *args):
        p = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def write(self, path, content):
        f = self.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def audit(self, base=None, head=None):
        p = subprocess.run([sys.executable, str(SCRIPT), "--base", base or self.base,
                            "--head", head or self.git("rev-parse", "HEAD")],
                           cwd=self.root, capture_output=True, text=True, timeout=30)
        return p.returncode, json.loads(p.stdout)

    def change(self, command, path=".github/workflows/safe.yml"):
        self.write(path, SAFE.replace("echo safe", command))
        self.commit()
        return self.audit()

    def test_unchanged(self):
        code, result = self.audit()
        self.assertEqual(code, 0)
        self.assertEqual(result["changed_workflows_checked"], 0)

    def test_safe_new(self):
        code, r = self.change("git fetch origin main", ".github/workflows/new.yml")
        self.assertEqual(code, 0, r)
        self.assertEqual(r["changed_workflows_checked"], 1)
        for k in ("authorizes_merge", "authorizes_deploy", "mutation_performed"):
            self.assertIs(r[k], False)

    def test_direct_push(self):
        code, r = self.change("git push origin HEAD:main")
        self.assertEqual(code, 1)
        self.assertEqual(r["findings"][0]["reasons"], ["git_push"])

    def test_force_push(self):
        self.assertEqual(self.change("git push -f origin HEAD:refs/heads/main")[0], 1)

    def test_git_c_push(self):
        self.assertEqual(self.change("git -C /tmp/repo push origin main")[0], 1)

    def test_dynamic_ref_push(self):
        self.assertEqual(self.change("git push origin HEAD:$BRANCH")[0], 1)

    def test_other_branch_push_conservatively_denied(self):
        self.assertEqual(self.change("git push origin HEAD:feature")[0], 1)

    def test_gh_refs_patch(self):
        self.assertEqual(self.change("gh api -X PATCH repos/x/y/git/refs/heads/main")[0], 1)

    def test_gh_api_dynamic_write(self):
        self.assertEqual(self.change("gh api -X POST \"$REPO/$ENDPOINT\"")[0], 1)

    def test_curl_refs_patch(self):
        self.assertEqual(self.change("curl -X PATCH https://api.github.com/repos/x/y/git/refs/heads/main")[0], 1)

    def test_legacy_byte_identical(self):
        self.write(".github/workflows/safe.yml", SAFE.replace("echo safe", "git push origin main"))
        base = self.commit()
        self.write(".github/workflows/new.yml", SAFE)
        head = self.commit()
        code, r = self.audit(base, head)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["legacy_workflows_not_rescanned"], 1)

    def test_edit_legacy_unsafe(self):
        self.write(".github/workflows/safe.yml", SAFE.replace("echo safe", "git push origin main"))
        base = self.commit()
        self.write(".github/workflows/safe.yml", SAFE.replace("echo safe", "git push origin main # changed"))
        head = self.commit()
        self.assertEqual(self.audit(base, head)[0], 1)

    def test_non_workflow_outside_scope(self):
        self.write("docs/example.md", "git push origin main")
        self.commit()
        self.assertEqual(self.audit()[0], 0)

    def test_unpinned_sha_denied(self):
        code, r = self.audit(base="main")
        self.assertEqual((code, r["reason"]), (1, "invalid_commit_sha"))

    def test_symlink_denied(self):
        target = self.root / ".github/workflows/safe.yml"
        target.unlink()
        target.symlink_to("/etc/passwd")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "nonregular_workflow")

    def test_invalid_utf8(self):
        self.write(".github/workflows/safe.yml", b"name: broken\n\xff")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "invalid_workflow_encoding")

    def test_embedded_nul(self):
        self.write(".github/workflows/safe.yml", b"name: broken\x00")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "invalid_workflow_blob")

    def test_large_blob(self):
        self.write(".github/workflows/safe.yml", SAFE + "# filler\n" * 80000)
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "invalid_workflow_blob")

    def test_missing_workflows(self):
        (self.root / ".github/workflows/safe.yml").unlink()
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "missing_workflows")

    def test_comment_is_ignored(self):
        self.write(".github/workflows/safe.yml", SAFE + "    # git push origin main\n")
        self.commit()
        self.assertEqual(self.audit()[0], 0)

    def test_git_global_no_pager_push_denied(self):
        self.assertEqual(self.change("git --no-pager push origin main")[0], 1)

    def test_single_quoted_push_argument_denied(self):
        self.assertEqual(self.change("git 'push' origin main")[0], 1)

    def test_double_quoted_push_argument_denied(self):
        self.assertEqual(self.change('git "push" origin main')[0], 1)

    def test_git_configuration_prefix_denied(self):
        self.assertEqual(self.change("git -c safe.directory=/tmp push origin main")[0], 1)

    def test_non_writer_git_global_option_allowed(self):
        self.assertEqual(self.change("git --no-pager status")[0], 0)

    def test_shell_backslash_newline_push_denied(self):
        self.assertEqual(self.change("git \\\n        push origin HEAD:main")[0], 1)

    def test_octokit_update_git_ref_denied(self):
        self.assertEqual(self.change("await github.rest.git.updateRef({owner: 'x', ref: 'heads/main'})")[0], 1)

    def test_octokit_create_git_ref_denied(self):
        self.assertEqual(self.change("await octokit.rest.git.createRef({ref: 'refs/heads/main'})")[0], 1)

    def test_octokit_delete_git_ref_denied(self):
        self.assertEqual(self.change("await octokit.git.deleteRef({ref: 'heads/main'})")[0], 1)

    def test_read_only_octokit_get_ref_allowed(self):
        self.assertEqual(self.change("await github.rest.git.getRef({ref: 'heads/main'})")[0], 0)

    def test_new_safe_composite_action_passes(self):
        self.write(".github/actions/verify/action.yml",
                   "name: check\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: git fetch origin main\n")
        self.commit()
        code, receipt = self.audit()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["changed_composites_checked"], 1)
        self.assertEqual(receipt["changed_workflows_checked"], 0)

    def test_new_writer_in_composite_action_denied(self):
        self.write(".github/actions/publish/action.yaml",
                   "name: unsafe\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: git push origin HEAD:main\n")
        self.commit()
        code, receipt = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(receipt["findings"][0]["reasons"], ["git_push"])

    def test_unchanged_legacy_writer_composite_passes(self):
        self.write(".github/actions/old/action.yml", "name: old\nrun: git push origin main\n")
        base = self.commit()
        self.write(".github/workflows/new.yml", SAFE)
        head = self.commit()
        code, r = self.audit(base, head)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["legacy_composites_not_rescanned"], 1)

    def test_symlinked_composite_action_denied(self):
        link = self.root / ".github/actions/demo/action.yml"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("/etc/passwd")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "nonregular_action_tree")

    def test_symlinked_composite_directory_denied(self):
        link = self.root / ".github/actions/demo"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("/tmp")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "nonregular_action_tree")

    def test_composite_cannot_replace_missing_workflow_inventory(self):
        self.write(".github/actions/demo/action.yml", "name: demo\n")
        (self.root / ".github/workflows/safe.yml").unlink()
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "missing_workflows")

    def test_graphql_cli_mutation_unprovable_denied(self):
        code, data = self.change("gh api graphql -f query='mutation {updateRef(input: {}) {clientMutationId}}'")
        self.assertEqual(code, 1)
        self.assertIn("dynamic_graphql_write_unverifiable", data["findings"][0]["reasons"])

    def test_graphql_cli_external_query_unprovable_denied(self):
        self.assertEqual(self.change("gh api graphql -F query=@request.graphql")[0], 1)

    def test_graphql_cli_flags_before_endpoint_denied(self):
        self.assertEqual(self.change("gh api --jq '.data' graphql -f query=@script.graphql")[0], 1)

    def test_graphql_octokit_endpoint_denied(self):
        self.assertEqual(self.change("await octokit.graphql(queryString)")[0], 1)

    def test_graphql_github_endpoint_denied(self):
        self.assertEqual(self.change("await github.graphql(queryString)")[0], 1)

    def test_graphql_direct_http_endpoint_denied(self):
        self.assertEqual(self.change("curl -X POST https://api.github.com/graphql -d @payload")[0], 1)

    def test_rest_read_only_gh_get_api_allowed(self):
        code, data = self.change("gh api repos/org/repo/commits/main")
        self.assertEqual(code, 0, data)

    def test_divergent_commit_history_rejected(self):
        self.write(".github/workflows/first.yml", SAFE)
        first = self.commit()
        self.git("checkout", "-qb", "independent-sibling", self.base)
        self.write(".github/workflows/second.yml", SAFE)
        second = self.commit()
        code, result = self.audit(base=first, head=second)
        self.assertEqual(code, 1)
        self.assertEqual(result["reason"], "unrelated_commit_history")

    def test_ancestor_base_is_admitted(self):
        self.write(".github/workflows/new.yml", SAFE)
        head = self.commit()
        code, result = self.audit(base=self.base, head=head)
        self.assertEqual(code, 0, result)

    def test_git_quoted_config_value_then_push_denied(self):
        code, result = self.change("git -c 'user.name=Alice Example' push origin HEAD:main")
        self.assertEqual(code, 1, result)

    def test_git_global_option_with_separate_path_then_push_denied(self):
        self.assertEqual(self.change("git --work-tree '/tmp/working copy' push origin HEAD:main")[0], 1)

    def test_git_quoted_config_value_non_writer_allowed(self):
        code, result = self.change("git -c 'user.name=Alice Example' status")
        self.assertEqual(code, 0, result)

    def test_git_quoted_opaque_command_denied(self):
        self.assertEqual(self.change("git 'unterminated quote")[0], 1)

    def test_git_status_followed_by_unrelated_push_word_allowed(self):
        code, result = self.change("git status && printf push")
        self.assertEqual(code, 0, result)

    def test_git_quoted_config_with_push_word_is_not_a_command(self):
        code, result = self.change("git -c 'alias.demo=echo push' status")
        self.assertEqual(code, 0, result)

    def test_quoted_git_revparse_substitution_allowed(self):
        code, receipt = self.change('actual="$(git rev-parse HEAD)"')
        self.assertEqual(code, 0, receipt)

    def test_quoted_git_fetch_substitution_allowed(self):
        code, receipt = self.change('source="$(git rev-parse --show-toplevel)"')
        self.assertEqual(code, 0, receipt)

    def test_quoted_git_push_substitution_denied(self):
        code, receipt = self.change('actual="$(git push origin HEAD:main)"')
        self.assertEqual(code, 1, receipt)
        self.assertIn("git_push", receipt["findings"][0]["reasons"])

    def test_shared_scan_deadline_expires_before_git_spawn(self):
        spec = importlib.util.spec_from_file_location("source_ratchet_under_test", SCRIPT)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SCAN_DEADLINE = time.monotonic() - 1
        with mock.patch.object(module.subprocess, "Popen") as spawn:
            with self.assertRaises(module.AdmissionError) as caught:
                module.git("status")
            self.assertEqual(str(caught.exception), "scan_deadline_exceeded")
            spawn.assert_not_called()


    def test_workflow_rechecks_remote_head_at_both_boundaries(self):
        workflow = SCRIPT.parents[1] / ".github/workflows/zcloud-direct-main-write-ratchet-20261015-w3.yml"
        content = workflow.read_text(encoding="utf-8")
        self.assertEqual(content.count("HEAD_REF: "), 2)
        self.assertEqual(content.count("ls-remote --exit-code https://github.com/Zennay/zCloud.git"), 2)
        self.assertEqual(content.count('test "$remote_head" = '), 2)

    def test_remote_branch_drift_changes_exact_head_proof(self):
        remote_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(remote_tmp.cleanup)
        bare = Path(remote_tmp.name) / "upstream.git"
        self.git("init", "--bare", str(bare))
        self.git("branch", "-M", "owner-branch")
        self.git("remote", "add", "origin", str(bare))
        self.git("push", "origin", "owner-branch")
        first = self.git("rev-parse", "HEAD")
        check_script = (
            'remote_head="$(git ls-remote --exit-code origin "refs/heads/$HEAD_REF")"; '
            'read -r remote_sha remote_ref <<< "$remote_head"; '
            'test "$remote_sha" = "$EVENT_HEAD"; '
            'test "$remote_ref" = "refs/heads/$HEAD_REF"'
        )
        def check(expected):
            env = os.environ.copy()
            env.update(EVENT_HEAD=expected, HEAD_REF="owner-branch")
            return subprocess.run(["bash", "-e", "-c", check_script], cwd=self.root,
                                  env=env, capture_output=True, text=True).returncode
        self.assertEqual(check(first), 0)
        self.write(".github/workflows/new.yml", SAFE)
        second = self.commit()
        self.git("push", "origin", "owner-branch")
        self.assertNotEqual(check(first), 0)
        self.assertEqual(check(second), 0)

    def test_inline_git_alias_to_push_denied(self):
        code, out = self.change("git -c 'alias.ship=push' ship origin HEAD:main")
        self.assertEqual(code, 1, out)
        self.assertIn("git_alias_indirect_write", out["findings"][0]["reasons"])

    def test_inline_git_shell_alias_to_push_denied(self):
        code, out = self.change("git -c 'alias.ship=!git push origin HEAD:main' ship")
        self.assertEqual(code, 1, out)
        self.assertIn("git_alias_indirect_write", out["findings"][0]["reasons"])

    def test_git_shell_alias_to_indirect_script_denied(self):
        code, out = self.change("git -c 'alias.ship=!bash ./publisher.sh' ship")
        self.assertEqual(code, 1, out)
        self.assertIn("git_alias_indirect_write", out["findings"][0]["reasons"])

    def test_git_configuration_read_only_alias_allowed(self):
        code, out = self.change("git -c 'alias.demo=echo safe' status")
        self.assertEqual(code, 0, out)

    def test_git_alias_value_with_push_as_argument_allowed(self):
        code, out = self.change("git -c 'alias.demo=echo push' status")
        self.assertEqual(code, 0, out)

    def test_octokit_branch_protection_mutation_denied(self):
        code, out = self.change("await github.rest.repos.updateBranchProtection({})")
        self.assertEqual(code, 1, out)
        self.assertIn("octokit_repository_governance_write", out["findings"][0]["reasons"])

    def test_octokit_ruleset_create_denied(self):
        code, out = self.change("await octokit.rest.repos.createRepoRuleset({})")
        self.assertEqual(code, 1, out)

    def test_octokit_ruleset_delete_denied(self):
        code, out = self.change("await github.repos.deleteRepoRuleset({})")
        self.assertEqual(code, 1, out)

    def test_octokit_read_branch_protection_allowed(self):
        code, out = self.change("await octokit.rest.repos.getBranchProtection({})")
        self.assertEqual(code, 0, out)

    def test_unicode_rtl_override_denied(self):
        self.write(".github/workflows/safe.yml", SAFE + "# harmless\u202evisible\n")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "opaque_workflow_encoding")

    def test_unicode_zero_width_joiner_denied(self):
        self.write(".github/workflows/safe.yml", SAFE + "# foo\u200dbar\n")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "opaque_workflow_encoding")

    def test_unicode_bidi_isolate_denied(self):
        self.write(".github/workflows/safe.yml", SAFE + "# \u2067foo\u2069\n")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "opaque_workflow_encoding")

    def test_non_ascii_accented_workflow_allowed(self):
        self.write(".github/workflows/safe.yml", SAFE + "# résumé café\n")
        self.commit()
        code, receipt = self.audit()
        self.assertEqual(code, 0, receipt)

    def test_unicode_legacy_unchanged_bytes_not_rescanned(self):
        self.write(".github/workflows/safe.yml", SAFE + "# foo\u200dbar\n")
        old = self.commit()
        self.write(".github/workflows/new.yml", SAFE)
        now = self.commit()
        code, receipt = self.audit(old, now)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["legacy_workflows_not_rescanned"], 1)

    def test_environment_config_count_denied(self):
        code, out = self.change("GIT_CONFIG_COUNT: '1'")
        self.assertEqual(code, 1, out)
        self.assertIn("git_environment_config_injection", out["findings"][0]["reasons"])

    def test_environment_config_key_denied(self):
        code, out = self.change("GIT_CONFIG_KEY_0: alias.ship")
        self.assertEqual(code, 1, out)

    def test_environment_config_value_denied(self):
        code, out = self.change("GIT_CONFIG_VALUE_0: push")
        self.assertEqual(code, 1, out)

    def test_environment_config_lowercase_denied(self):
        code, out = self.change("export git_config_count=2")
        self.assertEqual(code, 1, out)

    def test_ordinary_git_environment_allowed(self):
        code, out = self.change("GIT_TERMINAL_PROMPT: '0'")
        self.assertEqual(code, 0, out)

    def test_gh_pr_merge_direct_denied(self):
        code, r = self.change("gh pr merge 123 --squash")
        self.assertEqual(code, 1, r)
        self.assertIn("indirect_pr_merge_write", r["findings"][0]["reasons"])

    def test_gh_pr_merge_with_repo_selector_denied(self):
        code, r = self.change("gh --repo org/repo pr merge 123 --admin")
        self.assertEqual(code, 1, r)

    def test_gh_pr_merge_with_short_repo_selector_denied(self):
        self.assertEqual(self.change("gh -R org/repo pr merge 123")[0], 1)

    def test_octokit_pulls_merge_denied(self):
        code, r = self.change("await github.rest.pulls.merge({pull_number: 42})")
        self.assertEqual(code, 1, r)
        self.assertIn("indirect_pr_merge_write", r["findings"][0]["reasons"])

    def test_octokit_repos_merge_denied(self):
        self.assertEqual(self.change("await octokit.rest.repos.merge({base: 'main'})")[0], 1)

    def test_curl_rest_pulls_merge_denied(self):
        code, r = self.change("curl -X PUT https://api.github.com/repos/org/repo/pulls/42/merge")
        self.assertEqual(code, 1, r)
        self.assertIn("rest_merge_write", r["findings"][0]["reasons"])

    def test_curl_rest_merges_endpoint_denied(self):
        self.assertEqual(self.change("curl -X POST https://api.github.com/repos/org/repo/merges")[0], 1)

    def test_safe_gh_pr_view_allowed(self):
        code, r = self.change("gh pr view 123")
        self.assertEqual(code, 0, r)

    def test_safe_octokit_pulls_get_allowed(self):
        code, r = self.change("await github.rest.pulls.get({pull_number: 42})")
        self.assertEqual(code, 0, r)

    def test_git_send_pack_direct_write_denied(self):
        code, receipt = self.change("git send-pack origin HEAD:refs/heads/main")
        self.assertEqual(code, 1, receipt)
        self.assertIn("git_send_pack", receipt["findings"][0]["reasons"])

    def test_git_send_pack_quoted_arguments_denied(self):
        code, receipt = self.change("git -c 'user.name=Safe Tester' send-pack origin HEAD:refs/heads/main")
        self.assertEqual(code, 1, receipt)
        self.assertIn("git_send_pack", receipt["findings"][0]["reasons"])

    def test_git_send_pack_shell_continuation_denied(self):
        code, receipt = self.change("git \\\n        send-pack origin HEAD:main")
        self.assertEqual(code, 1, receipt)

    def test_git_send_pack_alias_denied(self):
        code, receipt = self.change("git -c 'alias.ship=send-pack' ship origin HEAD:main")
        self.assertEqual(code, 1, receipt)
        self.assertIn("git_alias_indirect_write", receipt["findings"][0]["reasons"])

    def test_git_send_pack_without_git_command_allowed(self):
        code, receipt = self.change("printf 'send-pack' > report.txt")
        self.assertEqual(code, 0, receipt)

    def test_safe_git_send_pack_in_quoted_config_value_allowed(self):
        code, receipt = self.change("git -c 'alias.demo=echo send-pack' status")
        self.assertEqual(code, 0, receipt)

    def test_rest_git_refs_collection_create_denied(self):
        code, out = self.change("curl -X POST https://api.github.com/repos/org/repo/git/refs")
        self.assertEqual(code, 1, out)
        self.assertIn("git_refs_api", out["findings"][0]["reasons"])

    def test_rest_git_tags_ref_mutation_denied(self):
        code, out = self.change("curl -X PATCH https://api.github.com/repos/org/repo/git/refs/tags/v1")
        self.assertEqual(code, 1, out)
        self.assertIn("git_refs_api", out["findings"][0]["reasons"])

    def test_rest_git_notes_ref_mutation_denied(self):
        self.assertEqual(self.change("curl -X DELETE https://api.github.com/repos/org/repo/git/refs/notes/test")[0], 1)

    def test_rest_git_refs_url_encoded_slash_denied(self):
        code, out = self.change("curl -X POST https://api.github.com/repos/org/repo/git/refs%2fheads")
        self.assertEqual(code, 1, out)

    def test_rest_git_refs_unicode_encoded_slash_denied(self):
        code, out = self.change(r'curl -X POST "https://api.github.com/repos/org/repo/git/refs\u002fheads"')
        self.assertEqual(code, 1, out)

    def test_rest_git_reflog_lookup_is_not_writer_denied(self):
        code, out = self.change("curl https://api.github.com/repos/org/repo/git/reflogs")
        self.assertEqual(code, 0, out)

    def test_python_subprocess_git_push_argv_denied(self):
        code, out = self.change("subprocess.run(['git', 'push', 'origin', 'HEAD:main'])")
        self.assertEqual(code, 1, out)
        self.assertIn("separate_argv_git_writer", out["findings"][0]["reasons"])

    def test_python_subprocess_git_send_pack_argv_denied(self):
        code, out = self.change('subprocess.check_call(["git", "send-pack", "origin"])')
        self.assertEqual(code, 1, out)
        self.assertIn("separate_argv_git_writer", out["findings"][0]["reasons"])

    def test_js_actions_exec_git_push_argv_denied(self):
        code, out = self.change('await exec.exec("git", ["push", "origin", "HEAD:main"])')
        self.assertEqual(code, 1, out)
        self.assertIn("separate_argv_git_writer", out["findings"][0]["reasons"])

    def test_js_spawn_git_send_pack_argv_denied(self):
        self.assertEqual(self.change("spawn('git', ['send-pack', 'origin'])")[0], 1)

    def test_python_subprocess_read_only_argv_allowed(self):
        code, out = self.change("subprocess.run(['git', 'status', '--short'])")
        self.assertEqual(code, 0, out)

    def test_js_actions_exec_read_only_argv_allowed(self):
        code, out = self.change('await exec.exec("git", ["rev-parse", "HEAD"])')
        self.assertEqual(code, 0, out)

    def test_no_raw_source_in_receipt(self):
        marker = "SECRET_TOKEN_SHOULD_NOT_BE_EMITTED"
        _, result = self.change("git push origin main # " + marker)
        self.assertNotIn(marker, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
