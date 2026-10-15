"""Real Git adversarial regressions: source-only immutable-action pin ratchet."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_actions_pin_ratchet_20261015 import (
    Refusal, compare, main, refs, snapshot, workflow_tree,
)


class ActionPinRatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.command("init", "-q")
        self.command("config", "user.name", "CI test")
        self.command("config", "user.email", "ci@example.invalid")
        self.path = self.root / ".github/workflows/proof.yml"
        self.path.parent.mkdir(parents=True)
        self.base = self.commit("name: proof\njobs:\n  demo:\n    steps:\n      - uses: actions/checkout@v4\n")

    def command(self, *parts):
        p = subprocess.run(["git", "-C", str(self.root), *parts],
                           check=True, capture_output=True, text=True)
        return p.stdout.strip()

    def commit(self, content):
        self.path.write_text(content, encoding="utf-8")
        self.command("add", ".github")
        self.command("commit", "-qm", "test")
        return self.command("rev-parse", "HEAD")

    def evaluate(self, base=None, head=None):
        result = compare(snapshot(self.root, base or self.base),
                         snapshot(self.root, head or self.command("rev-parse", "HEAD")))
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["deployment_authorized"])
        return result

    def test_existing_debt_allowed(self):
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_new_tag_denied(self):
        self.commit(self.path.read_text() + "      - uses: actions/cache@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_changed_tag_denied(self):
        self.commit(self.path.read_text().replace("checkout@v4", "checkout@v5"))
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_pin_removes_debt(self):
        pin = "abcdef0123456789abcdef0123456789abcdef01"
        self.commit(self.path.read_text().replace("checkout@v4", "checkout@" + pin))
        self.assertEqual(self.evaluate()["head_unpinned"], 0)

    def test_local_and_digest_docker_allowed(self):
        self.commit("steps:\n - uses: ./local/action\n - uses: docker://alpine@sha256:" + "1" * 64 + "\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_docker_dynamic_and_missing_ref_denied(self):
        self.commit("steps:\n - uses: docker://alpine:latest\n - uses: " + "$" + "{{ inputs.action }}\n - uses: actions/setup-python\n")
        result = self.evaluate()
        self.assertEqual(result["new_unpinned"], 3)

    def test_run_block_is_not_action(self):
        self.commit("steps:\n - run: |\n     uses: actions/cache@v4\n - uses: actions/checkout@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_quoted_action_and_comment_allowed(self):
        self.commit("steps:\n - uses: 'actions/checkout@" + "a" * 40 + "' # pin\n # uses: actions/cache@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_symlink_workflow_denied(self):
        self.path.unlink()
        self.path.symlink_to("/tmp/nonexistent")
        self.command("add", ".github")
        self.command("commit", "-qm", "symlink")
        with self.assertRaisesRegex(Refusal, "nonregular_workflow"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_worktree_modified_uncommitted_not_used(self):
        self.path.write_text(self.path.read_text() + " - uses: actions/cache@v3\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_actual_hosted_scan_red_path_keeps_failure_and_evidence(self):
        import os
        import shutil
        src_root = Path(__file__).resolve().parents[1]
        scanner_path = src_root / "scripts/zcloud_actions_pin_ratchet_20261015.py"
        destination = self.root / "scripts/zcloud_actions_pin_ratchet_20261015.py"
        destination.parent.mkdir()
        shutil.copyfile(scanner_path, destination)
        self.commit(self.path.read_text() + "      - uses: actions/cache@v4\n")
        head = self.command("rev-parse", "HEAD")
        workflow = (src_root / ".github/workflows/zcloud-actions-pin-no-new-debt-20261015.yml"
                    ).read_text(encoding="utf-8")
        section = workflow.split("name: Compare exact base and head source trees read only", 1)[1]
        script = section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in script.splitlines()
        ) + "\n"
        env = dict(os.environ, GITHUB_REPOSITORY="Zennay/zCloud",
                   GITHUB_EVENT_NAME="pull_request",
                   BASE_SHA=self.base, HEAD_SHA=head, RUNNER_TEMP=str(self.root))
        proc = subprocess.run(["bash", "-c", script], cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=25)
        self.assertEqual(proc.returncode, 1, proc.stderr + proc.stdout)
        report = self.root / "zcloud-actions-pin-evidence.json"
        self.assertTrue(report.is_file())
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["new_unpinned"], 1)
        self.assertFalse(payload["release_authorized"])
        self.assertFalse(payload["deployment_authorized"])
        self.assertFalse(payload["mutation_performed"])

    def test_new_composite_action_unpinned_step_denied(self):
        local = self.root / ".github/actions/reusable/action.yml"
        local.parent.mkdir(parents=True)
        local.write_text("runs:\n  using: composite\n  steps:\n"
                         "    - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "new composite action")
        result = self.evaluate()
        self.assertEqual(result["new_unpinned"], 1)
        self.assertEqual(result["examples"][0]["path"], ".github/actions/reusable/action.yml")

    def test_pinned_composite_action_allowed(self):
        local = self.root / ".github/actions/reusable/action.yaml"
        local.parent.mkdir(parents=True)
        local.write_text("runs:\n  using: composite\n  steps:\n"
                         "    - uses: actions/cache@" + "a" * 40 + "\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "pin composite step")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_legacy_composite_action_debt_unchanged_allowed(self):
        local = self.root / ".github/actions/reusable/action.yml"
        local.parent.mkdir(parents=True)
        local.write_text("runs:\n  using: composite\n  steps:\n"
                         "    - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "legacy composite action")
        base = self.command("rev-parse", "HEAD")
        self.commit(self.path.read_text() + "# harmless workflow comment\n")
        result = self.evaluate(base=base)
        self.assertTrue(result["ok"])
        self.assertEqual(result["base_unpinned"], 2)
        self.assertEqual(result["head_unpinned"], 2)

    def test_composite_metadata_symlink_refused(self):
        local = self.root / ".github/actions/reusable/action.yml"
        local.parent.mkdir(parents=True)
        local.symlink_to("/tmp/outside-action.yml")
        self.command("add", ".github")
        self.command("commit", "-qm", "symlink composite metadata")
        with self.assertRaisesRegex(Refusal, "nonregular_composite_action_source"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_composite_action_directory_symlink_refused(self):
        local = self.root / ".github/actions/reusable"
        local.parent.mkdir(parents=True)
        local.symlink_to("/tmp/outside-composite")
        self.command("add", ".github")
        self.command("commit", "-qm", "symlink composite directory")
        with self.assertRaisesRegex(Refusal, "nonregular_composite_action_source"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_unrelated_composite_file_is_not_counted_as_action(self):
        local = self.root / ".github/actions/reusable/README.md"
        local.parent.mkdir(parents=True)
        local.write_text("    - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "documentation only")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_composite_action_in_nonstandard_directory_denied(self):
        local = self.root / "tools/deploy/action.yml"
        local.parent.mkdir(parents=True)
        local.write_text("runs:\n  using: composite\n  steps:\n"
                         "    - uses: actions/cache@v4\n")
        self.command("add", "tools")
        self.command("commit", "-qm", "nested custom action")
        result = self.evaluate()
        self.assertEqual(result["new_unpinned"], 1)
        self.assertEqual(result["examples"][0]["path"], "tools/deploy/action.yml")

    def test_root_composite_action_yaml_denied(self):
        local = self.root / "action.yaml"
        local.write_text("runs:\n  using: composite\n  steps:\n"
                         "    - uses: actions/cache@v4\n")
        self.command("add", "action.yaml")
        self.command("commit", "-qm", "root composite action")
        result = self.evaluate()
        self.assertEqual(result["new_unpinned"], 1)
        self.assertEqual(result["examples"][0]["path"], "action.yaml")

    def test_nonstandard_action_metadata_symlink_rejected(self):
        local = self.root / "tools/deploy/action.yaml"
        local.parent.mkdir(parents=True)
        local.symlink_to("/tmp/outside-action.yaml")
        self.command("add", "tools")
        self.command("commit", "-qm", "symlinked action metadata")
        with self.assertRaisesRegex(Refusal, "nonregular_workflow"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_action_nonstandard_pinned_sha_allowed(self):
        local = self.root / "tools/deploy/action.yml"
        local.parent.mkdir(parents=True)
        local.write_text("runs:\n  using: composite\n  steps:\n"
                         "    - uses: actions/cache@" + "a" * 40 + "\n")
        self.command("add", "tools")
        self.command("commit", "-qm", "pinned reusable action")
        self.assertTrue(self.evaluate()["ok"])

    def test_local_action_symlink_direct_denied(self):
        link = self.root / "tools/external"
        link.parent.mkdir()
        link.symlink_to("/tmp/untrusted-action")
        self.command("add", "tools")
        self.commit(self.path.read_text() + "      - uses: ./tools/external\n")
        with self.assertRaisesRegex(Refusal, "nonregular_local_action_source"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_local_action_symlink_ancestor_denied(self):
        parent = self.root / "tools"
        parent.symlink_to("/tmp/untrusted-actions")
        self.command("add", "tools")
        self.commit(self.path.read_text() + "      - uses: ./tools/subdir\n")
        with self.assertRaisesRegex(Refusal, "nonregular_local_action_source"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_unrelated_symlink_ignored(self):
        link = self.root / "docs/latest"
        link.parent.mkdir()
        link.symlink_to("/tmp/unrelated")
        self.command("add", "docs")
        self.commit(self.path.read_text() + "      - uses: ./tools/safe\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_local_action_directory_not_symlink_allowed(self):
        local = self.root / "tools/safe/action.yml"
        local.parent.mkdir(parents=True)
        local.write_text("runs:\n  using: composite\n  steps: []\n")
        self.command("add", "tools")
        self.commit(self.path.read_text() + "      - uses: ./tools/safe\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_reject_composite_action_symlink_alias_via_dot_segment(self):
        link = self.root / "tools/external"
        link.parent.mkdir()
        link.symlink_to("/tmp/untrusted")
        self.command("add", "tools")
        self.commit(self.path.read_text() + "      - uses: ./tools/./external\n")
        with self.assertRaisesRegex(Refusal, "ambiguous_local_action_path"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_reject_local_action_path_equivalents_before_symlink_scan(self):
        for reference in ("./tools//external", "./tools/../external",
                          "./tools/.", "./tools/", "././tools", "./tools/./action"):
            with self.subTest(reference=reference):
                with self.assertRaises(Refusal):
                    refs("steps:\n - uses: " + reference, "proof.yml")

    def test_standard_hidden_local_action_path_remains_allowed(self):
        self.assertEqual(refs("steps:\n - uses: ./.github/actions/example", "proof.yml"), [])

    def test_yaml_folded_uses_does_not_mask_changed_external_reference(self):
        self.commit("steps:\n  - uses: >\n      actions/cache@v3\n")
        with self.assertRaisesRegex(Refusal, "ambiguous_uses_value"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_yaml_literal_and_chomp_uses_are_refused(self):
        for token in (">", "|", ">-", "|+", ">2", "|-2", ""):
            with self.subTest(token=token):
                with self.assertRaisesRegex(Refusal, "ambiguous_uses_value"):
                    refs("steps:\n - uses: " + token, "proof.yml")

    def test_unchanged_pinned_action_still_passes_source_scan(self):
        self.commit("steps:\n  - uses: actions/cache@" + "a" * 40 + "\n")
        self.assertTrue(self.evaluate()["ok"])

    def test_referenced_submodule_gitlink_denied(self):
        self.command("update-index", "--add", "--cacheinfo",
                     "160000," + "a" * 40 + ",tools/external")
        self.command("commit", "-qm", "register untrusted submodule")
        self.commit(self.path.read_text() + "      - uses: ./tools/external\n")
        with self.assertRaisesRegex(Refusal, "nonregular_local_action_source"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_submodule_gitlink_ancestor_denied(self):
        self.command("update-index", "--add", "--cacheinfo",
                     "160000," + "a" * 40 + ",tools")
        self.command("commit", "-qm", "register top-level gitlink")
        self.commit(self.path.read_text() + "      - uses: ./tools/action\n")
        with self.assertRaisesRegex(Refusal, "nonregular_local_action_source"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_unrelated_submodule_gitlink_does_not_block_scanning(self):
        self.command("update-index", "--add", "--cacheinfo",
                     "160000," + "a" * 40 + ",thirdparty/unrelated")
        self.command("commit", "-qm", "register unrelated gitlink")
        self.commit(self.path.read_text() + "      - uses: ./tools/safe\n")
        self.assertTrue(self.evaluate()["ok"])

    def test_oversized_git_blob_refused_before_buffering(self):
        from unittest.mock import patch
        import scripts.zcloud_actions_pin_ratchet_20261015 as scanner
        self.path.write_bytes(b"name: oversized\n" + b"x" * (scanner.MAX_FILE + 1))
        self.command("add", ".github")
        self.command("commit", "-qm", "large workflow")
        original = scanner.git
        blob_reads = []

        def source_only(root, *args):
            if args[:2] == ("cat-file", "blob"):
                blob_reads.append(args)
                raise AssertionError("oversize blob was read before size check")
            return original(root, *args)

        with patch.object(scanner, "git", side_effect=source_only):
            with self.assertRaisesRegex(Refusal, "workflow_bytes_limit"):
                snapshot(self.root, self.command("rev-parse", "HEAD"))
        self.assertEqual(blob_reads, [])

    def test_immutable_git_object_length_mismatch_refused(self):
        from unittest.mock import patch
        import scripts.zcloud_actions_pin_ratchet_20261015 as scanner
        original = scanner.git

        def wrong_length(root, *args):
            if args[:2] == ("cat-file", "-s"):
                return b"1\n"
            return original(root, *args)

        with patch.object(scanner, "git", side_effect=wrong_length):
            with self.assertRaisesRegex(Refusal, "git_blob_size_mismatch"):
                snapshot(self.root, self.base)

    def test_non_numeric_git_object_size_refused(self):
        from unittest.mock import patch
        import scripts.zcloud_actions_pin_ratchet_20261015 as scanner
        original = scanner.git

        def corrupted_length(root, *args):
            if args[:2] == ("cat-file", "-s"):
                return b"invalid\n"
            return original(root, *args)

        with patch.object(scanner, "git", side_effect=corrupted_length):
            with self.assertRaisesRegex(Refusal, "invalid_blob_size"):
                snapshot(self.root, self.base)

    def test_invalid_revision_refused(self):
        with self.assertRaisesRegex(Refusal, "invalid_revision"):
            workflow_tree(self.root, "main; rm -rf /")

    def test_new_workflow_debt_denied(self):
        (self.path.parent / "new.yml").write_text("steps:\n - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "new")
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_deleted_debt_allowed(self):
        replacement = self.path.parent / "safe.yml"
        replacement.write_text("steps:\n - uses: actions/checkout@" + "a" * 40 + "\n")
        self.path.unlink()
        self.command("add", "-A", ".github")
        self.command("commit", "-qm", "remove legacy while keeping workflow directory")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_duplicate_same_tag_increase_denied(self):
        self.commit(self.path.read_text() + " - uses: actions/checkout@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_cli_denial_no_raw_ref_echo(self):
        self.commit(self.path.read_text() + " - uses: actions/cache@v4\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = main(["--repo-root", str(self.root), "--base", self.base,
                           "--head", self.command("rev-parse", "HEAD")])
        self.assertEqual(result, 1)
        self.assertNotIn("actions/cache@v4", out.getvalue())
        self.assertFalse(json.loads(out.getvalue())["release_authorized"])

    def test_cli_refusal_exit_two(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = main(["--repo-root", str(self.root), "--base", "bad", "--head", self.base])
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(out.getvalue())["deployment_authorized"])

    def test_non_utf8_git_blob_refused(self):
        self.path.write_bytes(b"steps:\n - uses: \xff")
        self.command("add", ".github")
        self.command("commit", "-qm", "nonutf8")
        with self.assertRaisesRegex(Refusal, "workflow_non_utf8"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_nested_workflow_path_denied(self):
        deep = self.path.parent / "nested/test.yml"
        deep.parent.mkdir()
        deep.write_text("steps:\n - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "nested")
        with self.assertRaisesRegex(Refusal, "unexpected_workflow_path"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_oversized_reference_denied(self):
        with self.assertRaisesRegex(Refusal, "action_ref_oversize"):
            refs("steps:\n - uses: " + "a" * 513, "proof.yml")


    def test_flow_mapping_uses_refused(self):
        self.commit("steps:\n - {uses: actions/cache@v4}\n")
        with self.assertRaisesRegex(Refusal, "ambiguous_uses_syntax"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_quoted_uses_key_refused(self):
        self.commit("steps:\n - 'uses': actions/cache@v4\n")
        with self.assertRaisesRegex(Refusal, "ambiguous_uses_syntax"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_yaml_merge_alias_refused(self):
        self.commit("steps:\n - <<: *shared-action\n")
        with self.assertRaisesRegex(Refusal, "yaml_merge_key"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))


    def test_workflow_directory_symlink_refused(self):
        self.command("rm", "-rq", ".github/workflows")
        (self.root / ".github").mkdir(exist_ok=True)
        (self.root / ".github/workflows").symlink_to("/tmp/fake-workflows")
        self.command("add", ".github")
        self.command("commit", "-qm", "symlink workflow directory")
        with self.assertRaisesRegex(Refusal, "workflow_root_not_directory"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_missing_workflow_directory_refused(self):
        self.command("rm", "-rq", ".github/workflows")
        self.command("commit", "-qm", "remove all workflows")
        with self.assertRaises(Refusal):
            snapshot(self.root, self.command("rev-parse", "HEAD"))


    def test_reused_anchor_step_denied_without_new_uses_line(self):
        self.commit(
            "name: proof\njobs:\n  demo:\n    steps:\n"
            "      - &reusable\n        uses: actions/checkout@v4\n"
            "      - *reusable\n"
        )
        with self.assertRaisesRegex(Refusal, "yaml_alias"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_flow_sequence_alias_denied(self):
        self.commit(
            "steps:\n  - &cached\n    uses: actions/checkout@v4\n"
            "  - { name: again, source: [*cached] }\n"
        )
        with self.assertRaisesRegex(Refusal, "yaml_alias"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_flow_mapping_merge_denied(self):
        self.commit(
            "steps:\n - &base\n   uses: actions/checkout@v4\n"
            " - { <<: *base }\n"
        )
        with self.assertRaisesRegex(Refusal, "yaml_merge_key"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_inline_shell_literal_alias_remains_ignored(self):
        self.commit("steps:\n - run: |\n     echo '*not-a-yaml-alias'\n - uses: actions/checkout@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_commented_yaml_alias_not_executed(self):
        self.commit("steps:\n # - *inactive\n - uses: actions/checkout@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)



class HostedPinRatchetTrustTests(unittest.TestCase):
    """Lock the source-only CI trust boundary against regression."""

    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1]
                      / ".github/workflows/zcloud-actions-pin-no-new-debt-20261015.yml"
                      ).read_text(encoding="utf-8")

    def test_hosted_read_only_pr_admission(self):
        text = self.source
        self.assertIn("pull_request:", text)
        self.assertIn("push:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("github.event_name == 'push'", text)
        self.assertIn("name: Report outcome without creating any release authority", text)
        self.assertIn('".github/actions/**"', text)
        self.assertIn('"**/action.yml"', text)
        self.assertIn('"**/action.yaml"', text)
        self.assertIn('"action.yml"', text)
        self.assertIn('"action.yaml"', text)
        self.assertIn("contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        for denied in ("pull_request_target:", "workflow_dispatch:", "workflow_run:",
                       "permissions: write-all", "contents: write", "self-hosted",
                       "sudo ", "systemctl ", "gh workflow run"):
            self.assertNotIn(denied, text)

    def test_trusted_base_policy_and_bootstrap_warning(self):
        text = self.source
        self.assertIn('git cat-file -e "$BASE_SHA:$source_path"', text)
        self.assertIn('git show "$BASE_SHA:$source_path"', text)
        self.assertIn("Initial policy bootstrap is PR-supplied", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)
        self.assertIn('--base "$BASE_SHA" --head "$HEAD_SHA"', text)

    def test_canonical_main_freshness_rechecked_after_source_scan(self):
        text = self.source
        scan = text.index('python3 "$RUNNER_TEMP/trusted-pin-scanner.py"')
        final_check = text.index('name: Refuse stale canonical main and same-repository PR head after scan')
        self.assertLess(scan, final_check)
        self.assertIn("timeout 20 git ls-remote --exit-code origin refs/heads/main", text)
        self.assertIn('timeout 20 git ls-remote --exit-code origin "refs/heads/$HEAD_REF"', text)
        self.assertIn('if test "$HEAD_REPO" != "$GITHUB_REPOSITORY"; then', text)
        self.assertIn('if test "$current_main" != "$BASE_SHA"; then', text)
        self.assertIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", text)
        self.assertIn("exit 1", text[final_check:])


class HostedCurrentMainShellTests(unittest.TestCase):
    """Execute the actual hosted final shell step against local Git remotes."""

    def setUp(self):
        import os
        self.os = os
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "candidate"
        self.repo.mkdir()
        self.remote = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        self.git("init", "-q")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.invalid")
        (self.repo / "README.md").write_text("main rev 1\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "initial main")
        self.base = self.git("rev-parse", "HEAD")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "HEAD:refs/heads/main")
        self.git("push", "-q", "origin", "HEAD:refs/heads/topic")
        source = (Path(__file__).resolve().parents[1]
                  / ".github/workflows/zcloud-actions-pin-no-new-debt-20261015.yml"
                  ).read_text(encoding="utf-8")
        check = source.split("name: Refuse stale canonical main and same-repository PR head after scan", 1)[1]
        script = check.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        self.script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in script.splitlines()
        ) + "\n"

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              text=True, check=True, capture_output=True).stdout.strip()

    def execute(self, **override):
        env = dict(self.os.environ, GITHUB_REPOSITORY="Zennay/zCloud",
                   BASE_SHA=self.base, HEAD_SHA=self.base,
                   HEAD_REPO="Zennay/zCloud", HEAD_REF="topic")
        env.update(override)
        return subprocess.run(["bash", "-c", self.script], cwd=self.repo,
                              env=env, text=True, capture_output=True, timeout=25)

    def test_exact_current_main_remains_eligible_for_source_proof(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", result.stdout)

    def test_main_movement_after_checkout_denied(self):
        (self.repo / "README.md").write_text("main rev 2\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "new canonical main")
        self.git("push", "-q", "origin", "HEAD:refs/heads/main")
        # The checked-out candidate stays the immutable old head.
        self.git("reset", "--hard", self.base)
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Canonical main advanced", result.stdout)
        self.assertNotIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", result.stdout)

    def test_missing_canonical_main_denied(self):
        self.git("push", "-q", "origin", ":refs/heads/main")
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", result.stdout)


    def test_head_movement_after_checkout_denied(self):
        (self.repo / "README.md").write_text("topic rev 2\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "new PR head")
        self.git("push", "-q", "origin", "HEAD:refs/heads/topic")
        self.git("reset", "--hard", self.base)
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Pull request head moved", result.stdout)
        self.assertNotIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", result.stdout)

    def test_missing_pr_head_denied(self):
        self.git("push", "-q", "origin", ":refs/heads/topic")
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", result.stdout)

    def test_foreign_pr_head_denied_without_remote_contact(self):
        result = self.execute(HEAD_REPO="external/fork")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Foreign PR head", result.stdout)
        self.assertNotIn("ACTION_PIN_MAIN_AND_HEAD_CURRENT=1", result.stdout)


class HostedPinSummaryTests(unittest.TestCase):
    """Execute the actual non-authorizing Actions summary shell on adversarial data."""

    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parents[1]
                  / ".github/workflows/zcloud-actions-pin-no-new-debt-20261015.yml"
                  ).read_text(encoding="utf-8")
        src = source.split("name: Publish bounded Dutch evidence even when the source gate refuses", 1)[1]
        block = src.split("        run: |\n", 1)[1].split("\n  audit-pushed-main:", 1)[0]
        cls.script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in block.splitlines()
        ) + "\n"

    def summary(self, raw=None):
        import os
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "pin.json"
            destination = root / "summary.md"
            if raw is not None:
                report.write_text(raw, encoding="utf-8")
            env = dict(os.environ, REPORT_FILE=str(report),
                       GITHUB_STEP_SUMMARY=str(destination), RUNNER_TEMP=str(root))
            run = subprocess.run(["bash", "-c", self.script], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=25)
            self.assertEqual(run.returncode, 0, run.stderr)
            return destination.read_text(encoding="utf-8")

    def valid_report(self, **override):
        payload = {
            "policy": "zcloud-actions-pin-no-new-debt-v1",
            "base_unpinned": 72,
            "head_unpinned": 73,
            "new_unpinned": 1,
            "ok": False,
            "release_authorized": False,
            "deployment_authorized": False,
            "mutation_performed": False,
        }
        payload.update(override)
        return json.dumps(payload)

    def test_valid_denial_count_is_visible_without_permission(self):
        summary = self.summary(self.valid_report())
        self.assertIn("Nieuw gevonden risico | 1", summary)
        self.assertIn("Productie- of mergeautorisatie | **Nee**", summary)

    def test_missing_report_still_emits_deny_only_summary(self):
        summary = self.summary()
        self.assertIn("Nee — ontbrekend of onbetrouwbaar", summary)
        self.assertIn("Productie- of mergeautorisatie | **Nee**", summary)

    def test_duplicate_keys_rejected(self):
        raw = self.valid_report()
        raw = raw.replace('"release_authorized": false',
                          '"release_authorized": true, "release_authorized": false')
        summary = self.summary(raw)
        self.assertIn("Nee — ontbrekend of onbetrouwbaar", summary)
        self.assertNotIn("Nieuw gevonden risico | 1", summary)

    def test_incorrect_authorization_discredits_entire_report(self):
        summary = self.summary(self.valid_report(release_authorized=True))
        self.assertIn("Nee — ontbrekend of onbetrouwbaar", summary)
        self.assertIn("Productie- of mergeautorisatie | **Nee**", summary)

    def test_nonfinite_and_inconsistent_verdict_denied(self):
        raw = self.valid_report().replace('"new_unpinned": 1', '"new_unpinned": NaN')
        self.assertIn("Nee — ontbrekend of onbetrouwbaar", self.summary(raw))
        self.assertIn("Nee — ontbrekend of onbetrouwbaar",
                      self.summary(self.valid_report(ok=True)))

    def test_deeply_nested_json_cannot_suppress_deny_only_summary(self):
        # The runner must produce its denial summary even when json.loads
        # refuses adversarial recursion depth.
        hostile = "[" * 1800 + "0" + "]" * 1800
        summary = self.summary(hostile)
        self.assertIn("Nee — ontbrekend of onbetrouwbaar", summary)
        self.assertIn("Productie- of mergeautorisatie | **Nee**", summary)

    def test_private_raw_strings_never_interpolated_to_summary(self):
        raw = self.valid_report()
        raw = raw.replace('"ok": false', '"examples": [{"path": "TOP_SECRET_TOKEN"}], "ok": false')
        summary = self.summary(raw)
        self.assertNotIn("TOP_SECRET_TOKEN", summary)
        self.assertIn("Nieuw gevonden risico | 1", summary)


class HostedMainPushAuditTests(unittest.TestCase):
    """Run the exact main-push audit shell against real detached Git commits."""

    def setUp(self):
        import os
        import shutil
        self.os = os
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.remote = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        self.command("init", "-q")
        self.command("config", "user.name", "hosted pin test")
        self.command("config", "user.email", "pin@example.invalid")
        target = self.repo / ".github/workflows/example.yml"
        target.parent.mkdir(parents=True)
        target.write_text("jobs:\n demo:\n  steps:\n   - uses: actions/checkout@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "baseline")
        self.base = self.command("rev-parse", "HEAD")
        scanner = self.repo / "scripts/zcloud_actions_pin_ratchet_20261015.py"
        scanner.parent.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/zcloud_actions_pin_ratchet_20261015.py", scanner)
        self.command("add", "scripts")
        self.command("commit", "-qm", "scanner candidate")
        self.head = self.command("rev-parse", "HEAD")
        self.command("remote", "add", "origin", str(self.remote))
        self.command("push", "-q", "origin", "HEAD:refs/heads/main")
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/zcloud-actions-pin-no-new-debt-20261015.yml"
                    ).read_text(encoding="utf-8")
        section = workflow.split("name: Compare exact before and after commit tree read only", 1)[1]
        shell = section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        self.shell = "\n".join(line[10:] if line.startswith("          ") else line
                               for line in shell.splitlines()) + "\n"

    def command(self, *cmd):
        return subprocess.run(["git", "-C", str(self.repo), *cmd],
                              capture_output=True, text=True, check=True).stdout.strip()

    def execute(self, **updates):
        env = dict(self.os.environ,
                   GITHUB_REPOSITORY="Zennay/zCloud",
                   GITHUB_EVENT_NAME="push",
                   GITHUB_REF="refs/heads/main",
                   BEFORE_SHA=self.base, HEAD_SHA=self.head,
                   RUNNER_TEMP=str(self.root))
        env.update(updates)
        return subprocess.run(["bash", "-c", self.shell], cwd=self.repo,
                              env=env, text=True, capture_output=True, timeout=30)

    def test_clean_main_push_passes_no_new_debt(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.root / "main-pin-drift.json").read_text())
        self.assertEqual(report["new_unpinned"], 0)
        self.assertFalse(report["deployment_authorized"])

    def test_direct_main_push_with_new_floating_action_is_denied_with_evidence(self):
        target = self.repo / ".github/workflows/example.yml"
        target.write_text(target.read_text() + "   - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "unsafe direct push")
        self.command("push", "-q", "origin", "HEAD:refs/heads/main")
        result = self.execute(HEAD_SHA=self.command("rev-parse", "HEAD"))
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        report = json.loads((self.root / "main-pin-drift.json").read_text())
        self.assertEqual(report["new_unpinned"], 1)
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["mutation_performed"])

    def test_unknown_before_or_non_main_event_denied(self):
        self.assertNotEqual(self.execute(BEFORE_SHA="0" * 40).returncode, 0)
        self.assertNotEqual(self.execute(GITHUB_REF="refs/heads/other").returncode, 0)
        self.assertNotEqual(self.execute(GITHUB_EVENT_NAME="pull_request").returncode, 0)


class HostedMainPushSummaryTests(unittest.TestCase):
    """The advisory main push summary must remain redacted and deny-only."""

    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parents[1]
                  / ".github/workflows/zcloud-actions-pin-no-new-debt-20261015.yml"
                  ).read_text(encoding="utf-8")
        section = source.split("name: Report outcome without creating any release authority", 1)[1]
        block = section.split("        run: |\n", 1)[1]
        cls.script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in block.splitlines()
        ) + "\n"

    def summary(self, contents=None):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "main-pin-drift.json"
            output = root / "summary.md"
            if contents is not None:
                payload.write_text(contents, encoding="utf-8")
            env = dict(os.environ, RUNNER_TEMP=str(root),
                       EVIDENCE=str(payload), GITHUB_STEP_SUMMARY=str(output))
            proc = subprocess.run(["bash", "-c", self.script], cwd=root,
                                  capture_output=True, text=True, env=env, timeout=25)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return output.read_text(encoding="utf-8")

    @staticmethod
    def report(**changes):
        payload = {
            "policy": "zcloud-actions-pin-no-new-debt-v1",
            "base_unpinned": 72, "head_unpinned": 73,
            "new_unpinned": 1, "ok": False,
            "release_authorized": False,
            "deployment_authorized": False,
            "mutation_performed": False,
        }
        payload.update(changes)
        return json.dumps(payload)

    def test_main_push_advisory_valid_denial_clearly_visible(self):
        result = self.summary(self.report())
        self.assertIn("verwijzingen: 1", result)
        self.assertIn("deployautorisatie: **Nee**", result)
        self.assertIn("niet** geblokkeerd", result)

    def test_missing_or_array_evidence_denied_without_summary_crash(self):
        for raw in (None, '[]', '{"new_unpinned":1}', '{"new_unpinned":NaN}'):
            with self.subTest(value=raw):
                result = self.summary(raw)
                self.assertIn("verwijzingen: onbekend", result)
                self.assertIn("deployautorisatie: **Nee**", result)

    def test_duplicate_json_key_or_forged_permission_redacted(self):
        duplicated = self.report().replace(
            '"release_authorized": false',
            '"release_authorized": true, "release_authorized": false'
        )
        for raw in (duplicated, self.report(deployment_authorized=True),
                    self.report(ok=True)):
            with self.subTest(value=raw[:30]):
                result = self.summary(raw)
                self.assertIn("verwijzingen: onbekend", result)
                self.assertNotIn("verwijzingen: 1", result)

    def test_boolean_count_rejected_and_untrusted_text_never_echoed(self):
        self.assertIn("verwijzingen: onbekend",
                      self.summary(self.report(base_unpinned=True)))
        dirty = self.report()
        dirty = dirty.replace('"ok": false', '"notes":"MY_PRIVATE_TOKEN","ok": false')
        result = self.summary(dirty)
        self.assertIn("verwijzingen: 1", result)
        self.assertNotIn("MY_PRIVATE_TOKEN", result)


if __name__ == "__main__":
    unittest.main()
