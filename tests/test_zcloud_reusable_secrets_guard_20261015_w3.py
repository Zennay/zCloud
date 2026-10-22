"""Real-Git tests for source-only reusable-workflow secret inheritance ratchet."""
from __future__ import annotations

import contextlib
import io
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from scripts import zcloud_reusable_secrets_guard_20261015_w3 as guard
from scripts.zcloud_reusable_secrets_guard_20261015_w3 import (
    Denied, git, has_secret_inherit, inspect, main,
)

ROOT = ".github/workflows"


class WorkflowSecretInheritanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tests@localhost")
        self.git("config", "user.name", "workflow-ratchet-tests")
        self.write("README.md", "baseline\n")
        self.write(f"{ROOT}/baseline.yml", "name: baseline\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.check_output(["git", *args], text=True)

    @staticmethod
    def write(path, text):
        f = Path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD").strip()

    def test_simple_new_workflow_passes(self):
        self.write(f"{ROOT}/safe.yml", "name: safe\njobs:\n  smoke:\n    runs-on: ubuntu-latest\n")
        head = self.commit()
        res = inspect(self.base, head)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["changed_workflows_checked"], 1)
        self.assertFalse(res["authorizes_deploy"])
        self.assertFalse(res["mutation_performed"])

    def test_introduced_inherit_denied(self):
        self.write(f"{ROOT}/unsafe.yml", "jobs:\n  call:\n    uses: test/workflow.yml@abc\n    secrets: inherit\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_quoted_inherit_denied(self):
        for snippet in ("secrets: 'inherit'", '"secrets": "inherit"', "secrets: { foo: inherit }"):
            with self.subTest(snippet=snippet):
                self.assertTrue(has_secret_inherit(f"jobs:\n  a:\n    {snippet}\n"))

    def test_comment_only_does_not_block(self):
        self.assertFalse(has_secret_inherit("# secrets: inherit\nname: safe\n"))

    def test_indented_yaml_scalar_denied(self):
        self.assertTrue(has_secret_inherit("secrets:\n  inherit\n"))

    def test_missing_secret_keyword_safe(self):
        self.assertFalse(has_secret_inherit("run: echo inherit\n"))

    def test_historical_unmodified_unsafe_blob_grandfathered(self):
        self.write(f"{ROOT}/legacy.yml", "jobs:\n  call:\n    secrets: inherit\n")
        self.base = self.commit()
        self.write(f"{ROOT}/new.yml", "name: new-safe\n")
        head = self.commit()
        result = inspect(self.base, head)
        self.assertEqual(result["changed_workflows_checked"], 1)

    def test_edited_legacy_inheritance_denied(self):
        self.write(f"{ROOT}/legacy.yml", "jobs:\n  call:\n    secrets: inherit\n")
        self.base = self.commit()
        self.write(f"{ROOT}/legacy.yml", "name: edited\njobs:\n  call:\n    secrets: inherit\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_non_workflow_not_scanned(self):
        self.write("docs/reusable-example.md", "secrets: inherit\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 0)

    def test_invalid_utf8_fails_closed(self):
        path = Path(f"{ROOT}/binary.yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"name: \xff\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "utf8"):
            inspect(self.base, head)

    def test_oversized_workflow_denied(self):
        self.write(f"{ROOT}/huge.yaml", "x" * 524289)
        head = self.commit()
        with self.assertRaisesRegex(Denied, "oversized"):
            inspect(self.base, head)

    def test_symlink_workflow_denied(self):
        path = Path(f"{ROOT}/link.yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink("../../README.md", path)
        head = self.commit()
        with self.assertRaisesRegex(Denied, "unsupported_workflow"):
            inspect(self.base, head)

    def test_invalid_commit_identity_denied(self):
        with self.assertRaisesRegex(Denied, "invalid_commit_identity"):
            inspect(self.base, self.base)
        with self.assertRaisesRegex(Denied, "invalid_commit_identity"):
            inspect("../main", self.base)

    def test_cli_redacts_workflow_and_denies(self):
        self.write(f"{ROOT}/leak.yml", "jobs:\n  call:\n    secrets: inherit  # SENSITIVE_MARKER\n")
        head = self.commit()
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            status = main(["--base", self.base, "--head", head])
        self.assertEqual(status, 1)
        self.assertIn('"authorizes_deploy": false', sink.getvalue())
        self.assertNotIn("SENSITIVE_MARKER", sink.getvalue())

    def test_complex_yaml_key_denied_from_real_git(self):
        self.write(f"{ROOT}/complex.yml", "? secrets\n: inherit\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_unicode_escaped_key_denied_from_real_git(self):
        self.write(f"{ROOT}/encoded.yaml", '"se\\u0063rets": "in\\u0068erit"\n')
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_unicode_escape_in_opaque_yaml_is_conservatively_denied(self):
        self.write(f"{ROOT}/opaque.yml", 'name: "\\u0073ensitive"\n')
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_ci_runs_for_every_main_pr_without_path_filter(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml").read_text()
        event = workflow.split("permissions:", 1)[0]
        self.assertIn("pull_request:", event)
        self.assertIn("branches: [main]", event)
        self.assertNotIn("    paths:", event)
        self.assertNotIn("    paths-ignore:", event)

    def test_ci_binds_both_remote_refs_before_and_after_scan(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml").read_text()
        exact_main = """git ls-remote --exit-code origin refs/heads/main)"""
        exact_head = """git ls-remote --exit-code origin "refs/heads/$HEAD_REF")"""
        self.assertEqual(workflow.count(exact_main), 2)
        self.assertEqual(workflow.count(exact_head), 2)
        self.assertEqual(workflow.count("refs/heads/$HEAD_REF"), 4)
        self.assertIn("git check-ref-format --branch", workflow)
        self.assertIn("HEAD_REF: ${{ github.event.pull_request.head.ref }}", workflow)
        self.assertIn("git rev-parse HEAD", workflow)

    def test_ci_is_hosted_readonly_never_promotes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml").read_text()
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_exact_remote_ref_receipt_does_not_accept_moving_branch(self):
        remote = Path(self.tmp.name) / ".git" / "proof-remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", self.base + ":refs/heads/main")
        original = self.git("ls-remote", "--exit-code", "origin", "refs/heads/main").strip()
        self.assertEqual(original, self.base + "\trefs/heads/main")
        self.write("README.md", "revision moved\n")
        new_head = self.commit()
        self.git("push", "-q", "origin", new_head + ":refs/heads/main")
        updated = self.git("ls-remote", "--exit-code", "origin", "refs/heads/main").strip()
        self.assertNotEqual(updated, original)
        self.assertEqual(updated, new_head + "\trefs/heads/main")

    def test_missing_workflow_directory_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        Path(ROOT).rmdir()
        head = self.commit()
        with self.assertRaisesRegex(Denied, "missing_workflow_directory"):
            inspect(self.base, head)

    def test_symlinked_workflows_root_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        Path(ROOT).rmdir()
        os.symlink("../README.md", ROOT)
        head = self.commit()
        with self.assertRaisesRegex(Denied, "non_directory_workflow_root"):
            inspect(self.base, head)

    def test_symlinked_dotgithub_root_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        Path(ROOT).rmdir()
        Path(".github").rmdir()
        os.symlink("README.md", ".github")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "non_directory_workflow_root"):
            inspect(self.base, head)

    def test_clean_directory_root_keeps_readonly_receipt(self):
        self.write(f"{ROOT}/new.yaml", "name: additional\\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["workflows_examined"], 2)
        self.assertFalse(inspect(self.base, head)["mutation_performed"])

    def test_directory_with_only_readme_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        self.write(f"{ROOT}/README.md", "workflow definitions disabled\\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "empty_workflow_inventory"):
            inspect(self.base, head)

    def test_deletion_of_one_workflow_preserves_remaining_inventory(self):
        self.write(f"{ROOT}/retired.yml", "name: retired\\n")
        self.base = self.commit()
        Path(f"{ROOT}/retired.yml").unlink()
        head = self.commit()
        result = inspect(self.base, head)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["workflows_examined"], 1)

    def test_git_output_budget_rejects_large_real_blob_early(self):
        self.write(f"{ROOT}/size.yml", "z" * 150000)
        self.commit()
        oid = self.git("rev-parse", "HEAD:.github/workflows/size.yml").strip()
        with self.assertRaisesRegex(Denied, "source_budget_exceeded"):
            git("cat-file", "blob", oid, limit=32)
        # A terminated oversized command must not poison later evidence reads.
        self.assertEqual(git("rev-parse", "HEAD", limit=41).decode().strip(),
                         self.git("rev-parse", "HEAD").strip())

    def test_git_budget_zero_allows_successful_empty_output(self):
        head = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(git("merge-base", "--is-ancestor", head, head, limit=0), b"")
        with self.assertRaisesRegex(Denied, "source_budget_exceeded"):
            git("rev-parse", "HEAD", limit=0)

    def test_git_budget_exact_output_boundary(self):
        self.assertEqual(len(git("rev-parse", "HEAD", limit=41)), 41)
        with self.assertRaisesRegex(Denied, "source_budget_exceeded"):
            git("rev-parse", "HEAD", limit=40)

    def test_git_budget_invalid_parameters_fail_closed(self):
        for budget in (-1, 1.5, True, 16 * 1024 * 1024 + 1):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(Denied, "invalid_source_budget"):
                    git("rev-parse", "HEAD", limit=budget)

    def test_git_subprocess_nonzero_denied(self):
        with self.assertRaisesRegex(Denied, "git_source_unavailable"):
            git("rev-parse", "--verify", "refs/heads/does-not-exist")

    def test_git_subprocess_no_stdin_prompt_or_optional_locks(self):
        actual_popen = subprocess.Popen
        observed = []

        def wrapped(*args, **kwargs):
            observed.append(kwargs)
            return actual_popen(*args, **kwargs)

        with mock.patch("scripts.zcloud_reusable_secrets_guard_20261015_w3.subprocess.Popen", side_effect=wrapped):
            result = git("rev-parse", "HEAD", limit=41)
        self.assertEqual(len(result), 41)
        self.assertEqual(len(observed), 1)
        invocation = observed[0]
        self.assertEqual(invocation["stdin"], subprocess.DEVNULL)
        self.assertEqual(invocation["stderr"], subprocess.DEVNULL)
        self.assertTrue(invocation["close_fds"])
        self.assertEqual(invocation["env"]["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(invocation["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_git_replace_ref_cannot_hide_inherited_secrets(self):
        path = f"{ROOT}/adversarial.yml"
        self.write(path, "jobs:\n  call:\n    secrets: inherit\n")
        head = self.commit()
        original_oid = self.git("rev-parse", "HEAD:" + path).strip()
        replacement = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], input="name: safe\n",
            text=True, stdout=subprocess.PIPE, check=True,
        ).stdout.strip()
        self.git("replace", original_oid, replacement)
        # Plain Git follows refs/replace and would report the innocent blob.
        substituted = self.git("cat-file", "blob", original_oid)
        self.assertIn("name: safe", substituted)
        # The scanner must read the real object associated with its SHA.
        self.assertIn(b"secrets: inherit", git("cat-file", "blob", original_oid))
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_git_child_environment_forbids_object_replacement(self):
        observed = []
        real_popen = subprocess.Popen

        def wrapped(*args, **kwargs):
            observed.append(kwargs.get("env", {}))
            return real_popen(*args, **kwargs)

        with mock.patch(
            "scripts.zcloud_reusable_secrets_guard_20261015_w3.subprocess.Popen",
            side_effect=wrapped,
        ):
            self.assertEqual(len(git("rev-parse", "HEAD", limit=41)), 41)
        self.assertEqual(observed[0]["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_git_tree_replacement_cannot_hide_unsafe_workflow(self):
        path = f"{ROOT}/unsafe-tree.yml"
        self.write(path, "jobs:\n  call:\n    secrets: inherit\n")
        unsafe_head = self.commit()
        unsafe_tree = self.git("rev-parse", unsafe_head + ":.github/workflows").strip()
        self.write(path, "name: safe\n")
        safe_head = self.commit()
        safe_tree = self.git("rev-parse", safe_head + ":.github/workflows").strip()
        self.assertNotEqual(unsafe_tree, safe_tree)
        # Capture immutable tree contents BEFORE installing a replacement:
        # rev-parse <commit>:path itself follows replacement refs afterward.
        unsafe_blob_oid = self.git("rev-parse", unsafe_head + ":" + path).strip()
        safe_blob_oid = self.git("rev-parse", safe_head + ":" + path).strip()
        self.git("replace", unsafe_tree, safe_tree)
        # Plain Git sees the replacement tree despite the unsafe tree ID.
        self.assertIn(safe_blob_oid, self.git("cat-file", "-p", unsafe_tree))
        # The scanner's Git wrapper must expose the original tree instead.
        self.assertIn(unsafe_blob_oid, git("cat-file", "-p", unsafe_tree).decode())
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, unsafe_head)

    def test_changed_git_blob_bytes_denied_by_sha_even_same_size(self):
        self.write(f"{ROOT}/verified.yml", "name: safe\n")
        head = self.commit()
        original_git = guard.git

        def spoof(*args, **kwargs):
            data = original_git(*args, **kwargs)
            if args[:2] == ("cat-file", "blob"):
                return b"x" * len(data)
            return data

        with mock.patch.object(guard, "git", side_effect=spoof):
            with self.assertRaisesRegex(Denied, "git_blob_integrity"):
                inspect(self.base, head)

    def test_git_blob_size_claim_mismatch_denied(self):
        self.write(f"{ROOT}/verified.yml", "name: safe\n")
        head = self.commit()
        original_git = guard.git

        def spoof_size(*args, **kwargs):
            if args[:2] == ("cat-file", "-s"):
                return b"1\n"
            return original_git(*args, **kwargs)

        with mock.patch.object(guard, "git", side_effect=spoof_size):
            with self.assertRaisesRegex(Denied, "git_blob_integrity"):
                inspect(self.base, head)

    def test_newline_workflow_filename_denied(self):
        self.write(f"{ROOT}/unsafe\nname.yml", "name: safe\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "ambiguous_workflow_path"):
            inspect(self.base, head)

    def test_bidi_override_workflow_filename_denied(self):
        self.write(f"{ROOT}/unsafe\u202e.yml", "name: safe\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "ambiguous_workflow_path"):
            inspect(self.base, head)

    def test_control_character_non_yaml_filename_denied(self):
        self.write(f"{ROOT}/invisible\tname.txt", "note\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "ambiguous_workflow_path"):
            inspect(self.base, head)

    def test_legitimate_utf8_filename_allowed(self):
        self.write(f"{ROOT}/café.yml", "name: safe\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["status"], "pass")

    def test_inherited_git_config_and_trace_dropped_before_launch(self):
        trace = Path(self.tmp.name) / "forbidden-git-trace.json"
        actual = subprocess.Popen
        captured = []

        def observer(*args, **kwargs):
            captured.append(kwargs["env"])
            return actual(*args, **kwargs)

        inherited = {
            "GIT_TRACE2_EVENT": str(trace),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.email",
            "GIT_CONFIG_VALUE_0": "unexpected@example.org",
            "GIT_OBJECT_DIRECTORY": "/does/not/exist",
            "GIT_NO_REPLACE_OBJECTS": "0",
        }
        with mock.patch.dict(os.environ, inherited):
            with mock.patch.object(guard.subprocess, "Popen", side_effect=observer):
                self.assertEqual(len(git("rev-parse", "HEAD", limit=41)), 41)
        self.assertEqual(len(captured), 1)
        child = captured[0]
        self.assertEqual(child["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(child["GIT_OPTIONAL_LOCKS"], "0")
        for key in inherited:
            if key != "GIT_NO_REPLACE_OBJECTS":
                self.assertNotIn(key, child)
        self.assertFalse(trace.exists(), "read-only source scan wrote Git trace output")

    def test_inherited_git_config_cannot_override_source_objects(self):
        original = git("rev-parse", "HEAD", limit=41)
        with mock.patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.bare",
            "GIT_CONFIG_VALUE_0": "true",
        }):
            self.assertEqual(git("rev-parse", "HEAD", limit=41), original)

    def test_ci_binds_canonical_origin_on_both_sides_of_source_tests(self):
        workflow = (
            Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml"
        ).read_text(encoding="utf-8")
        needle = 'test "$(git remote get-url origin)" = "https://github.com/Zennay/zCloud"'
        self.assertEqual(workflow.count(needle), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', workflow)

    def test_real_git_detects_remote_origin_redirection(self):
        expected = "https://github.com/Zennay/zCloud"
        self.git("remote", "add", "origin", expected)
        self.assertEqual(self.git("remote", "get-url", "origin").strip(), expected)
        self.git("remote", "set-url", "origin", "https://github.com/untrusted/fake")
        self.assertNotEqual(self.git("remote", "get-url", "origin").strip(), expected)

    def test_zero_width_name_format_character_denied(self):
        self.write(f"{ROOT}/hidden\u200bname.yml", "name: safe\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "ambiguous_workflow_path"):
            inspect(self.base, head)

    def test_soft_hyphen_format_character_denied(self):
        self.write(f"{ROOT}/hidden\u00adname.yml", "name: safe\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "ambiguous_workflow_path"):
            inspect(self.base, head)

    def test_new_casefold_filename_collision_denied(self):
        self.write(f"{ROOT}/Policy.yml", "name: first\n")
        self.write(f"{ROOT}/policy.yml", "name: second\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "new_confusable_workflow_paths"):
            inspect(self.base, head)

    def test_new_nfc_nfd_filename_collision_denied(self):
        self.write(f"{ROOT}/café.yml", "name: first\n")
        self.write(f"{ROOT}/cafe\u0301.yml", "name: second\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "new_confusable_workflow_paths"):
            inspect(self.base, head)

    def test_unchanged_legacy_case_collision_grandfathered(self):
        self.write(f"{ROOT}/Alias.yml", "name: old uppercase\n")
        self.write(f"{ROOT}/alias.yml", "name: old lowercase\n")
        self.base = self.commit()
        self.write(f"{ROOT}/unrelated.yml", "name: new safe\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 1)

    def test_modified_legacy_case_collision_fails_closed(self):
        self.write(f"{ROOT}/Alias.yml", "name: old uppercase\n")
        self.write(f"{ROOT}/alias.yml", "name: old lowercase\n")
        self.base = self.commit()
        self.write(f"{ROOT}/Alias.yml", "name: edited\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "new_confusable_workflow_paths"):
            inspect(self.base, head)

    def test_safe_distinct_unicode_workflow_paths_preserved(self):
        self.write(f"{ROOT}/café.yml", "name: accented\n")
        self.write(f"{ROOT}/cafe.yml", "name: unaccented\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 2)

    def test_git_global_config_does_not_contaminate_scanner(self):
        home = Path(self.tmp.name) / "untrusted-home"
        home.mkdir()
        (home / ".gitconfig").write_text(
            "[ratchet]\n  untrustedMarker = infected\n", encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            outside = self.git("config", "--get", "ratchet.untrustedMarker").strip()
            self.assertEqual(outside, "infected")
            with self.assertRaisesRegex(Denied, "git_source_unavailable"):
                git("config", "--get", "ratchet.untrustedMarker")
            self.assertEqual(len(git("rev-parse", "HEAD", limit=41)), 41)

    def test_git_no_implicit_fetch_and_no_system_config_contract(self):
        original = subprocess.Popen
        envs = []

        def inspect_popen(*args, **kwargs):
            envs.append(kwargs.get("env", {}))
            return original(*args, **kwargs)

        with mock.patch.object(guard.subprocess, "Popen", side_effect=inspect_popen):
            self.assertEqual(len(git("rev-parse", "HEAD", limit=41)), 41)
        self.assertEqual(len(envs), 1)
        env = envs[0]
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_CONFIG_SYSTEM"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")

    def test_pass_receipt_exposes_exact_unrescanned_legacy_count(self):
        self.write(f"{ROOT}/new.yml", "name: clean\n")
        head = self.commit()
        receipt = inspect(self.base, head)
        self.assertEqual(receipt["workflows_examined"], 2)
        self.assertEqual(receipt["changed_workflows_checked"], 1)
        self.assertEqual(receipt["legacy_workflows_not_rescanned"], 1)
        self.assertEqual(receipt["source_scope"], "changed_workflows_only")
        self.assertFalse(receipt["authorizes_merge"])

    def test_legacy_risky_workflows_never_reported_as_scanned(self):
        self.write(f"{ROOT}/historic.yml", "secrets: inherit\n")
        self.base = self.commit()
        self.write(f"{ROOT}/new.yml", "name: safe\n")
        head = self.commit()
        receipt = inspect(self.base, head)
        self.assertEqual(receipt["changed_workflows_checked"], 1)
        self.assertEqual(receipt["legacy_workflows_not_rescanned"], 2)

    def test_multiple_unsafe_sources_emit_only_stable_path_fingerprints(self):
        names = (f"{ROOT}/alpha.yml", f"{ROOT}/beta.yaml")
        for name in names:
            self.write(name, "jobs:\n  call:\n    secrets: inherit\n  note: SENSITIVE_TOKEN\n")
        head = self.commit()
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            result = main(["--base", self.base, "--head", head])
        self.assertEqual(result, 1)
        output = sink.getvalue()
        receipt = json.loads(output)
        self.assertEqual(receipt["reason"], "modified_workflow_may_inherit_secrets")
        self.assertEqual(receipt["suspect_workflow_path_sha256_prefixes"], [
            hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
            for name in names
        ])
        for name in names:
            self.assertNotIn(name, output)
        self.assertNotIn("SENSITIVE_TOKEN", output)
        self.assertFalse(receipt["mutation_performed"])
        self.assertFalse(receipt["authorizes_deploy"])

    def test_non_inheritance_denial_never_discloses_path_or_content(self):
        self.write(f"{ROOT}/too-large.yml", "CONFIDENTIAL_MARKER" * 30000)
        head = self.commit()
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            self.assertEqual(main(["--base", self.base, "--head", head]), 1)
        receipt = json.loads(sink.getvalue())
        self.assertEqual(receipt["reason"], "oversized_workflow")
        self.assertNotIn("suspect_workflow_path_sha256_prefixes", receipt)
        self.assertNotIn("CONFIDENTIAL_MARKER", sink.getvalue())

    def test_expired_entire_scan_budget_denies_before_launch(self):
        self.write(f"{ROOT}/new.yml", "name: safe\n")
        head = self.commit()
        with mock.patch.object(guard, "SCAN_BUDGET_SECONDS", -1):
            with mock.patch.object(guard.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(Denied, "source_scan_timeout"):
                    inspect(self.base, head)
                popen.assert_not_called()

    def test_expired_subprocess_deadline_denies_before_launch(self):
        expired = time.monotonic() - 1
        with mock.patch.object(guard.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(Denied, "source_scan_timeout"):
                git("rev-parse", "HEAD", limit=41, overall_deadline=expired)
            popen.assert_not_called()

    def test_every_git_inspection_shares_one_global_deadline(self):
        self.write(f"{ROOT}/new.yml", "name: safe\n")
        head = self.commit()
        reader = guard.git
        observed = []

        def record(*args, **kwargs):
            observed.append(kwargs.get("overall_deadline"))
            return reader(*args, **kwargs)

        with mock.patch.object(guard, "git", side_effect=record):
            self.assertEqual(inspect(self.base, head)["status"], "pass")
        self.assertGreaterEqual(len(observed), 9)
        self.assertTrue(all(isinstance(t, float) for t in observed))
        self.assertEqual(len(set(observed)), 1)

    def test_deleted_legacy_inheritance_is_not_new_exposure(self):
        self.write(f"{ROOT}/legacy.yml", "secrets: inherit\n")
        self.base = self.commit()
        Path(f"{ROOT}/legacy.yml").unlink()
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 0)


if __name__ == "__main__":
    unittest.main()
