"""Real-Git and adversarial offline source checks. No external network required."""
import importlib.util
import json
import sys
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/zcloud_shell_expression_ratchet_20261015.py"
spec = importlib.util.spec_from_file_location("guard", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()


class RealGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.tmp.name)
        git("init", "-q")
        git("config", "user.email", "guard@example.invalid")
        git("config", "user.name", "Guard")
        self.workflow = Path(".github/workflows/proof.yml")
        self.workflow.parent.mkdir(parents=True)

    def tearDown(self):
        os.chdir(self.previous)
        self.tmp.cleanup()

    def commit(self, data=None):
        if data is not None:
            self.workflow.write_text(data)
        git("add", "-A")
        git("commit", "--allow-empty", "-qm", "fixture")
        return git("rev-parse", "HEAD")

    def test_safe_environment_indirection(self):
        base = self.commit("name: x\njobs:\n  x:\n    steps:\n      - run: echo safe\n")
        head = self.commit('jobs:\n  x:\n    steps:\n      - env:\n          TITLE: ${{ github.event.issue.title }}\n        run: printf "%s" "$TITLE"\n')
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_new_inline_event_interpolation_denied(self):
        base = self.commit("name: x\nsteps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.event.pull_request.title }}\n")
        report = guard.audit(base, head)
        self.assertEqual(1, report["new_risks"])
        self.assertEqual([str(self.workflow)], report["affected_workflows"])
        self.assertFalse(report["deployment_authorized"])

    def test_nested_multiline_expression_denied(self):
        base = self.commit("steps:\n  - run: |\n      echo ok\n")
        head = self.commit("steps:\n  - run: |-\n      echo '${{ format(\n        '{0}', github.event.comment.body\n      ) }}'\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_old_finding_not_new(self):
        expr = "steps:\n  - run: echo ${{ github.event.issue.title }}\n"
        base = self.commit(expr)
        head = self.commit(expr + "# harmless new comment\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_new_input_in_separate_workflow_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        other = Path(".github/workflows/extra.yml")
        other.write_text("steps:\n  - run: echo ${{ inputs.unsafe }}\n")
        head = self.commit()
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_replacing_one_risk_with_another_is_not_invisible(self):
        base = self.commit("steps:\n  - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("steps:\n  - run: echo ${{ github.event.issue.body }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_yaml_alias_new_hazard(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: *shell_command\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_symlink_workflow_fails_closed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        self.workflow.unlink()
        self.workflow.symlink_to("/tmp/evil")
        head = self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "non_regular_workflow"):
            guard.audit(base, head)

    def test_nonancestor_rejected(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo safe2\n")
        with self.assertRaisesRegex(guard.EvidenceError, "base_not_head_ancestor"):
            guard.audit(head, base)

    def test_no_workflow_refuses(self):
        self.commit("steps:\n  - run: echo safe\n")
        self.workflow.unlink()
        head = self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "empty_workflow_inventory"):
            guard.snapshot(head)

    def test_deleted_old_risk_is_allowed(self):
        base = self.commit("steps:\n  - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("steps:\n  - run: echo safe\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_blob_size_refused_pre_read(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        self.workflow.write_text("#" * (guard.MAX_BLOB_BYTES + 1))
        head = self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "workflow_blob_too_large"):
            guard.audit(base, head)

    def test_bracket_notation_event_property_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github['event']['issue']['title'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracket_notation_input_property_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit('steps:\n  - run: echo ${{ inputs["payload"] }}\n')
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_dynamic_bracket_property_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github[format('event')] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_spaced_dot_property_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github . event . issue . title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_single_quoted_run_key_is_detected(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - 'run': echo ${{ github.event.issue.title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_double_quoted_run_key_is_detected(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit('steps:\n  - "run": echo ${{ github.event.issue.title }}\n')
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_inline_flow_mapping_run_expression_is_not_skipped(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - {name: guarded, run: echo ${{ github.event.issue.title }}}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_new_safe_inline_flow_run_is_fail_closed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - {name: probe, 'run': echo safe}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_replacing_existing_flow_run_does_not_hide_new_risk(self):
        base = self.commit("steps:\n  - {run: echo original}\n")
        head = self.commit("steps:\n  - {run: echo ${{ github.event.issue.body }}}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_existing_flow_run_unrelated_comment_conservatively_denied(self):
        # With unknown YAML flow structure, any source edit invalidates baseline.
        source = "steps:\n  - {run: echo safe}\n"
        base = self.commit(source)
        head = self.commit(source + "# changed file despite unchanged flow mapping\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_risky_expression_same_name_changed_shell_body_denied(self):
        base = self.commit("steps:\n  - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("steps:\n  - run: printf dangerous ${{ github.event.issue.title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_risky_expression_in_new_step_denied_even_if_legacy_exists(self):
        base = self.commit("steps:\n  - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("steps:\n  - run: echo ${{ github.event.issue.title }}\n  - run: printf ${{ github.event.issue.title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_same_legacy_run_with_adjacent_safe_step_remains_baseline(self):
        expression = "steps:\n  - run: echo ${{ github.event.issue.title }}\n"
        base = self.commit(expression)
        head = self.commit(expression + "  - run: echo separately safe\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_second_unterminated_expression_after_valid_close_denied(self):
        base = self.commit("steps:\n  - run: echo ${{ 1 + 1 }}\n")
        head = self.commit("steps:\n  - run: echo ${{ 1 + 1 }} ${{ github.event.issue.title\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_changed_yaml_alias_is_new_ambiguous_shell_debt(self):
        base = self.commit("steps:\n  - run: *old_command\n")
        head = self.commit("steps:\n  - run: *new_command\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_changed_computed_property_is_new_ambiguous_debt(self):
        base = self.commit("steps:\n  - run: echo ${{ github[format('event')] }}\n")
        head = self.commit("steps:\n  - run: echo ${{ github[format('head_ref')] }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_incomplete_expression_relocated_is_new_risk(self):
        base = self.commit("steps:\n  - run: echo ${{ broken\n")
        head = self.commit("steps:\n  - run: printf ${{ broken\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_unicode_escaped_run_key_is_not_bypass(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "u0072un"
        head = self.commit('steps:\n  - "' + key + '": echo ${{ github.event.issue.body }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_hex_escaped_run_key_is_not_bypass(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "x72un"
        head = self.commit('steps:\n  - "' + key + '": echo ${{ inputs.from_pr }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_safe_yaml_escaped_run_key_remains_no_new_interpolation(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "u0072un"
        head = self.commit('steps:\n  - "' + key + '": echo safe\n')
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_run_alias_anchor_edit_is_new_ambiguous_debt(self):
        base = self.commit("steps:\n  - env:\n      VALUE: &cmd echo safe\n    run: *cmd\n")
        head = self.commit("steps:\n  - env:\n      VALUE: &cmd echo ${{ github.event.issue.body }}\n    run: *cmd\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_unmodified_alias_source_is_not_new_debt(self):
        source = "steps:\n  - env:\n      VALUE: &cmd echo safe\n    run: *cmd\n"
        base = self.commit(source)
        head = self.commit(source)
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_flow_style_escaped_run_key_not_ignored(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "u0072un"
        head = self.commit('steps:\n  - {name: guard, "' + key + '": echo ${{ github.event.issue.body }}}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_explicit_yaml_run_key_is_fail_closed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - ? run\n    : echo ${{ github.event.issue.body }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_explicit_yaml_run_key_legacy_unchanged_is_not_new_debt(self):
        source = "steps:\n  - ? run\n    : echo safe\n"
        base = self.commit(source)
        head = self.commit(source)
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_explicit_yaml_run_key_changed_value_is_new_debt(self):
        base = self.commit("steps:\n  - ? run\n    : echo safe\n")
        head = self.commit("steps:\n  - ? run\n    : echo riskier\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def cli(self, base, head):
        return subprocess.run([sys.executable, str(MODULE), "--base", base, "--head", head],
                              capture_output=True, text=True, timeout=10, check=False)

    def test_cli_safe_commit_exits_zero_without_authority(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo safer\n")
        response = self.cli(base, head)
        report = json.loads(response.stdout)
        self.assertEqual(0, response.returncode)
        self.assertTrue(report["inventory_complete"])
        self.assertEqual(0, report["new_risks"])
        self.assertFalse(report["deployment_authorized"])

    def test_cli_new_untrusted_interpolation_exits_one(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.event.issue.body }}\n")
        response = self.cli(base, head)
        report = json.loads(response.stdout)
        self.assertEqual(1, response.returncode)
        self.assertEqual(1, report["new_risks"])
        self.assertFalse(report["release_authorized"])

    def test_cli_missing_git_commit_exits_two_and_denies_authority(self):
        self.commit("steps:\n  - run: echo safe\n")
        response = self.cli("0" * 40, "1" * 40)
        report = json.loads(response.stdout)
        self.assertEqual(2, response.returncode)
        self.assertFalse(report["inventory_complete"])
        self.assertEqual("base_not_head_ancestor", report["error"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["deployment_authorized"])

    def test_new_github_ref_interpolation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.ref }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_new_github_base_ref_interpolation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.base_ref }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracket_notation_github_ref_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github['ref'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_serialized_entire_github_context_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo '${{ toJSON(github) }}'\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_safe_github_sha_interpolation_not_misidentified(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.sha }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_spaced_serialized_github_context_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: printf '%s' '${{ toJSON ( github ) }}'\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracket_base_ref_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github['base_ref'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_safe_event_ref_in_environment_not_counted_as_shell(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - env:\n      BRANCH: ${{ github.ref }}\n    run: printf '%s' \"$BRANCH\"\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_existing_ref_risk_stays_grandfathered_only_when_unchanged(self):
        old = "steps:\n  - run: echo ${{ github.ref }}\n"
        base = self.commit(old)
        head = self.commit(old + "  - run: echo safe\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_safe_github_sha_serialization_not_whole_context(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(github.sha) }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_report_lists_risky_workflows_without_leaking_shell_commands(self):
        expression = "steps:\n  - run: echo ${{ github.event.issue.title }}\n"
        base = self.commit(expression)
        head = self.commit(expression)
        report = guard.audit(base, head)
        expectation = [{"path": str(self.workflow), "risk_count": 1}]
        self.assertEqual(expectation, report["baseline_risky_workflows"])
        self.assertEqual(expectation, report["candidate_risky_workflows"])
        self.assertNotIn("github.event.issue.title", json.dumps(report))

    def test_risk_inventory_lists_paths_in_canonical_sorted_order(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        self.workflow.write_text("steps:\n  - run: echo ${{ github.ref }}\n")
        extra = Path(".github/workflows/alpha.yml")
        extra.write_text("steps:\n  - run: echo ${{ inputs.attacker }}\n")
        head = self.commit()
        report = guard.audit(base, head)
        self.assertEqual([
            {"path": str(extra), "risk_count": 1},
            {"path": str(self.workflow), "risk_count": 1},
        ], report["candidate_risky_workflows"])
        self.assertEqual([], report["baseline_risky_workflows"])
        self.assertEqual(2, report["new_risks"])

    def test_clean_workflow_inventory_is_empty(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo harmless\n")
        report = guard.audit(base, head)
        self.assertEqual([], report["baseline_risky_workflows"])
        self.assertEqual([], report["candidate_risky_workflows"])

    def test_explicit_escaped_yaml_run_key_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        run_key = chr(92) + "u0072un"
        head = self.commit('steps:\n  - ? "' + run_key + '"\n    : echo ${{ github.ref }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_explicit_escaped_yaml_run_key_legacy_unchanged_allowed(self):
        run_key = chr(92) + "u0072un"
        source = 'steps:\n  - ? "' + run_key + '"\n    : echo safe\n'
        base = self.commit(source)
        head = self.commit(source)
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_explicit_escaped_yaml_run_key_edit_denied(self):
        run_key = chr(92) + "x72un"
        base = self.commit('steps:\n  - ? "' + run_key + '"\n    : echo safe\n')
        head = self.commit('steps:\n  - ? "' + run_key + '"\n    : echo ${{ inputs.payload }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_composite_action_run_injection_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        action = Path(".github/actions/local/action.yml")
        action.parent.mkdir(parents=True)
        action.write_text("runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: echo ${{ github.event.issue.title }}\n")
        head = self.commit()
        report = guard.audit(base, head)
        self.assertEqual(1, report["new_risks"])
        self.assertEqual([str(action)], report["affected_workflows"])
        self.assertEqual([{"path": str(action), "risk_count": 1}],
                         report["candidate_risky_workflows"])

    def test_nested_action_yaml_inputs_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        action = Path("packages/cli/action.yaml")
        action.parent.mkdir(parents=True)
        action.write_text("runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: printf '%s' '${{ inputs.from_pr }}'\n")
        head = self.commit()
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_composite_action_env_indirection_remains_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        action = Path(".github/actions/echo/action.yml")
        action.parent.mkdir(parents=True)
        action.write_text("runs:\n  using: composite\n  steps:\n    - shell: bash\n      env:\n        TITLE: ${{ github.event.issue.title }}\n      run: printf '%s' \"$TITLE\"\n")
        head = self.commit()
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_legacy_composite_action_shell_edit_is_new_risk(self):
        action = Path(".github/actions/legacy/action.yml")
        action.parent.mkdir(parents=True)
        action.write_text("runs:\n  using: composite\n  steps:\n    - run: echo ${{ github.event.issue.title }}\n      shell: bash\n")
        base = self.commit("steps:\n  - run: echo safe\n")
        action.write_text("runs:\n  using: composite\n  steps:\n    - run: printf ${{ github.event.issue.title }}\n      shell: bash\n")
        head = self.commit()
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_deleted_composite_action_is_not_new_debt(self):
        action = Path(".github/actions/legacy/action.yml")
        action.parent.mkdir(parents=True)
        action.write_text("runs:\n  using: composite\n  steps:\n    - run: echo ${{ github.ref }}\n      shell: bash\n")
        base = self.commit("steps:\n  - run: echo safe\n")
        action.unlink()
        head = self.commit()
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_symlinked_action_metadata_fails_closed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        action = Path(".github/actions/unsafe/action.yml")
        action.parent.mkdir(parents=True)
        action.symlink_to("/tmp/secret")
        head = self.commit()
        with self.assertRaisesRegex(guard.EvidenceError, "non_regular_workflow"):
            guard.audit(base, head)

    def test_unrelated_yaml_file_not_scanned_as_action(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        action = Path("packages/cli/docs.yml")
        action.parent.mkdir(parents=True)
        action.write_text("run: echo ${{ github.event.issue.title }}\n")
        head = self.commit()
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_identical_untrusted_run_moved_to_other_job_is_new_debt(self):
        base = self.commit("jobs:\n  audit:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("jobs:\n  privileged:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_identical_untrusted_run_under_renamed_job_is_new_debt(self):
        base = self.commit("jobs:\n  old:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("jobs:\n  new:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_legacy_run_unrelated_job_added_does_not_create_debt(self):
        base = self.commit("jobs:\n  audit:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n")
        head = self.commit("jobs:\n  audit:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n  separate:\n    steps:\n      - run: echo safe\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_multiline_flow_run_edit_denied(self):
        """Changing an expression on a continuation line must not reuse a legacy flow finding."""
        base = self.commit("jobs:\n  old:\n    steps:\n      - {name: probe, run: 'echo\n          ${{ github.event.issue.title }}'}\n")
        head = self.commit("jobs:\n  old:\n    steps:\n      - {name: probe, run: 'echo\n          ${{ inputs.payload }}'}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_identical_flow_run_relocated_to_other_job_denied(self):
        original = "jobs:\n  old:\n    steps:\n      - {name: probe, run: echo safe}\n"
        changed = "jobs:\n  privileged:\n    steps:\n      - {name: probe, run: echo safe}\n"
        base = self.commit(original)
        head = self.commit(changed)
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_existing_flow_run_anchor_edit_denied(self):
        base = self.commit("jobs:\n  x:\n    steps:\n      - {run: *cmd}\n  env:\n    CMD: &cmd safe\n")
        head = self.commit("jobs:\n  x:\n    steps:\n      - {run: *cmd}\n  env:\n    CMD: &cmd dangerous\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_unchanged_flow_workflow_is_grandfathered(self):
        initial = "jobs:\n  x:\n    steps:\n      - {run: echo safe}\n"
        base = self.commit(initial)
        head = self.commit(initial)
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_workflow_ref_shell_interpolation_denied(self):
        # Workflow refs contain the potentially attacker-selected branch name.
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.workflow_ref }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracketed_workflow_ref_shell_interpolation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github['workflow_ref'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_action_ref_shell_interpolation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.action_ref }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_safe_workflow_sha_shell_interpolation_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.workflow_sha }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_yaml_long_unicode_escaped_run_key_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "U00000072un"
        head = self.commit('steps:\n  - "' + key + '": echo ${{ github.event.issue.body }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_long_unicode_second_letter_escaped_run_key_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = "r" + chr(92) + "U00000075n"
        head = self.commit('steps:\n  - "' + key + '": echo ${{ inputs.value }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_flow_style_long_unicode_escaped_run_key_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "U00000072un"
        head = self.commit('steps:\n  - {name: audit, "' + key + '": echo ${{ github.event.issue.body }}}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_explicit_long_unicode_escaped_run_key_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        key = chr(92) + "U00000072un"
        head = self.commit('steps:\n  - ? "' + key + '"\n    : echo ${{ inputs.value }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_quoted_escape_dollar_denied(self):
        """YAML decoding can produce an expression token absent from raw source."""
        base = self.commit("steps:\n  - run: echo safe\n")
        value = "\\u0024" + "{{ github.event.issue.title }}"
        head = self.commit('steps:\n  - run: "echo ' + value + '"\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_quoted_hex_escape_dollar_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        value = "\\x24" + "{{ inputs.pr_data }}"
        head = self.commit('steps:\n  - run: "echo ' + value + '"\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_quoted_long_unicode_escaped_expression_delimiters_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        value = "\\U00000024\\U0000007b\\U0000007b github.event.issue.body }}"
        head = self.commit('steps:\n  - run: "echo ' + value + '"\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_quoted_escaped_property_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        value = "$" + "{{ github.\\u0065vent.issue.title }}"
        head = self.commit('steps:\n  - run: "echo ' + value + '"\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_yaml_single_quoted_literal_escape_not_interpolated(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        value = "\\u0024" + "{{ github.event.issue.title }}"
        head = self.commit("steps:\n  - run: 'echo " + value + "'\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_yaml_double_quoted_safe_plain_run_preserved(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit('steps:\n  - run: "printf safe"\n')
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_legacy_risky_step_condition_relaxed_denied(self):
        unsafe = "$" + "{{ github.event.issue.body }}"
        base = self.commit("jobs:\n  x:\n    steps:\n      - name: gated\n        if: false\n        run: echo " + unsafe + "\n")
        head = self.commit("jobs:\n  x:\n    steps:\n      - name: gated\n        if: true\n        run: echo " + unsafe + "\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_risky_step_shell_changed_denied(self):
        unsafe = "$" + "{{ inputs.from_pr }}"
        base = self.commit("jobs:\n  x:\n    steps:\n      - name: guarded\n        shell: bash\n        run: echo " + unsafe + "\n")
        head = self.commit("jobs:\n  x:\n    steps:\n      - name: guarded\n        shell: pwsh\n        run: echo " + unsafe + "\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_identical_unsafe_step_moved_past_safe_step_denied(self):
        unsafe = "$" + "{{ github.event.issue.title }}"
        base = self.commit("steps:\n  - run: echo " + unsafe + "\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo safe\n  - run: echo " + unsafe + "\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_identical_unsafe_step_renamed_denied(self):
        unsafe = "$" + "{{ github.event.issue.title }}"
        base = self.commit("steps:\n  - name: guarded\n    run: echo " + unsafe + "\n")
        head = self.commit("steps:\n  - name: privileged\n    run: echo " + unsafe + "\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_unsafe_step_append_safe_step_allowed(self):
        unsafe = "$" + "{{ github.event.issue.title }}"
        base = self.commit("steps:\n  - name: current\n    run: echo " + unsafe + "\n")
        head = self.commit("steps:\n  - name: current\n    run: echo " + unsafe + "\n  - name: safe\n    run: echo harmless\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_new_alias_of_unsafe_job_denied(self):
        unsafe = "$" + "{{ github.event.issue.body }}"
        base_source = ("jobs:\n  base: &template\n    runs-on: ubuntu-latest\n"
                       "    steps:\n      - run: echo " + unsafe + "\n")
        base = self.commit(base_source)
        head = self.commit(base_source + "  cloned: *template\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_alias_retarget_between_existing_job_anchors_denied(self):
        unsafe = "$" + "{{ github.event.issue.body }}"
        common = ("jobs:\n  a: &a\n    steps:\n      - run: echo " + unsafe + "\n"
                  "  b: &b\n    steps:\n      - run: echo safe\n")
        base = self.commit(common + "  copied: *b\n")
        head = self.commit(common + "  copied: *a\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_unchanged_legacy_job_alias_no_new_debt(self):
        unsafe = "$" + "{{ github.event.issue.body }}"
        source = ("jobs:\n  base: &base\n    steps:\n      - run: echo " + unsafe
                  + "\n  copied: *base\n")
        base = self.commit(source)
        head = self.commit(source)
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_shell_string_asterisk_does_not_create_yaml_alias_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: printf '*shell_literal'\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_steps_output_direct_shell_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ steps.build.outputs.pr_body }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_needs_output_direct_shell_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ needs.collect.outputs.raw_title }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracketed_steps_output_direct_shell_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ steps['get-data']['outputs']['result'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracketed_needs_output_direct_shell_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ needs['download'].outputs['contents'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_dynamic_matrix_value_direct_shell_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ matrix.pull_request_payload }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_prior_steps_output_does_not_hide_changed_command(self):
        expression = "${{ steps.download.outputs.payload }}"
        base = self.commit("steps:\n  - run: echo " + expression + "\n")
        head = self.commit("steps:\n  - run: printf execute " + expression + "\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_safe_steps_outcome_and_needs_result_remain_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ steps.audit.outcome }} ${{ needs.audit.result }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_safe_sha_and_numeric_matrix_env_remain_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - env:\n      SAFE_SHA: ${{ github.sha }}\n      MATRIX_SIZE: ${{ matrix.size }}\n    run: echo $SAFE_SHA $MATRIX_SIZE\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_json_serialized_needs_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(needs) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_json_serialized_steps_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(steps) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_spaced_json_serialized_needs_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON ( needs ) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_dynamic_step_output_selector_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ steps[format('download')].outputs.payload }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_dynamic_needs_output_selector_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ needs[format('compile')].outputs.payload }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_safe_fixed_steps_status_access_remains_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ steps.lint.conclusion }} ${{ needs.test.result }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_wildcard_needs_output_aggregation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ join(needs.*.outputs.payload, ' ') }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_wildcard_steps_output_aggregation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ join(steps.*.outputs.payload, ' ') }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_safe_wildcard_needs_results_not_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ join(needs.*.result, ' ') }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_direct_env_shell_interpolation_is_new_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ env.PR_TITLE }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracket_env_shell_interpolation_is_new_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ env['PR-BODY'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_spaced_env_shell_interpolation_is_new_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ env . CONTENT }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_computed_env_property_is_new_ambiguous_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ env[format('PAYLOAD')] }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_serialized_entire_env_shell_interpolation_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(env) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_literal_shell_env_variable_stays_safe(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - env:\n      PR_TITLE: ${{ github.event.issue.title }}\n    run: printf '%s' \"$PR_TITLE\"\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_existing_unsafe_env_shell_interpolation_is_grandfathered_only_unchanged(self):
        unsafe = "steps:\n  - run: echo ${{ env.PR_TITLE }}\n"
        base = self.commit(unsafe)
        head = self.commit(unsafe + "# harmless extra comment\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])
        next_head = self.commit(unsafe.replace("echo", "eval"))
        self.assertEqual(1, guard.audit(base, next_head)["new_risks"])

    def test_direct_secret_in_shell_is_new_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ secrets.RELEASE_TOKEN }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_direct_repository_variable_in_shell_is_new_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: eval ${{ vars.DYNAMIC_COMMAND }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracketed_secret_expression_in_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ secrets['DEPLOY-TOKEN'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_bracketed_repository_variable_in_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ vars['PROBE-FLAG'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_dynamic_secret_lookup_is_ambiguous_shell_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ secrets[format('TOKEN')] }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_serialized_vars_or_secrets_context_in_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(vars) }} ${{ toJSON(secrets) }}\n")
        self.assertEqual(2, guard.audit(base, head)["new_risks"])

    def test_secret_env_indirection_remains_outside_direct_shell_guard(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - env:\n      TOKEN: ${{ secrets.RELEASE_TOKEN }}\n    run: test -n \"$TOKEN\"\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_nested_needs_job_json_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(needs.compile) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_nested_steps_job_json_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(steps.download) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_nested_needs_bracket_json_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(needs['build-test']) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_nested_steps_bracket_json_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(steps['get-data']) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_nested_wildcard_job_json_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(needs.*) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_nested_job_status_json_remains_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(needs.compile.result) }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_nested_step_status_json_remains_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ toJSON(steps.check.outcome) }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_nested_job_json_legacy_moved_shell_is_new_debt(self):
        unsafe = "steps:\n  - run: echo ${{ toJSON(needs.build) }}\n"
        base = self.commit(unsafe)
        head = self.commit(unsafe.replace("echo", "eval"))
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_github_workflow_title_direct_shell_is_new_debt(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.workflow }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_github_workflow_title_bracket_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: printf ${{ github['workflow'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_github_token_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.token }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_github_token_bracket_direct_shell_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github['token'] }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_github_workflow_sha_remains_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.workflow_sha }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_workflow_title_via_quoted_shell_env_remains_allowed(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - env:\n      WORKFLOW_TITLE: ${{ github.workflow }}\n    run: printf '%s\\n' \"$WORKFLOW_TITLE\"\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_risk_kind_report_classifies_inline_expression_without_leak(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github.event.secret_message }}\n")
        report = guard.audit(base, head)
        self.assertEqual([], report["baseline_risk_kinds"])
        self.assertEqual([{"kind": "direct_interpolation", "count": 1}],
                         report["candidate_risk_kinds"])
        self.assertNotIn("secret_message", json.dumps(report))

    def test_risk_kind_report_classifies_incomplete_and_ambiguous_shell(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: *anchor_name\n  - run: echo ${{ missing\n")
        report = guard.audit(base, head)
        self.assertEqual([{"kind": "incomplete_expression", "count": 1},
                          {"kind": "yaml_structure_ambiguity", "count": 1}],
                         report["candidate_risk_kinds"])
        self.assertEqual(2, sum(row["count"] for row in report["candidate_risk_kinds"]))

    def test_risk_kind_report_classifies_computed_property_ambiguity(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ github[format('event')] }}\n")
        report = guard.audit(base, head)
        self.assertEqual([{"kind": "dynamic_property_ambiguity", "count": 1}],
                         report["candidate_risk_kinds"])
        self.assertNotIn("format", json.dumps(report))

    def test_risk_kind_report_grandfathers_exact_legacy_findings(self):
        source = "steps:\n  - run: echo ${{ env.OLD_SECRET }}\n"
        base = self.commit(source)
        head = self.commit(source + "# harmless comment\n")
        report = guard.audit(base, head)
        expected = [{"kind": "direct_interpolation", "count": 1}]
        self.assertEqual(expected, report["baseline_risk_kinds"])
        self.assertEqual(expected, report["candidate_risk_kinds"])
        self.assertEqual(0, report["new_risks"])

    def test_new_risk_kinds_identify_only_added_findings(self):
        source = "steps:\n  - run: echo ${{ env.OLD }}\n"
        base = self.commit(source)
        head = self.commit(source + "  - run: echo ${{ vars.NEW_TOKEN }}\n")
        report = guard.audit(base, head)
        self.assertEqual(1, report["new_risks"])
        self.assertEqual([{"kind": "direct_interpolation", "count": 1}],
                         report["new_risk_kinds"])
        self.assertNotIn("NEW_TOKEN", json.dumps(report))

    def test_new_risk_kinds_empty_on_no_new_debt(self):
        source = "steps:\n  - run: echo ${{ env.OLD }}\n"
        base = self.commit(source)
        head = self.commit(source + "# same risk\n")
        report = guard.audit(base, head)
        self.assertEqual([], report["new_risk_kinds"])
        self.assertEqual(0, report["new_risks"])

    def test_new_risk_kinds_mixed_shell_and_ambiguity(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ inputs.NEW_SECRET }}\n  - run: *unsafe\n")
        report = guard.audit(base, head)
        self.assertEqual(2, report["new_risks"])
        self.assertEqual([{"kind": "direct_interpolation", "count": 1},
                          {"kind": "yaml_structure_ambiguity", "count": 1}],
                         report["new_risk_kinds"])

    def test_tagged_plain_run_key_is_not_invisible(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - !!str run: echo ${{ inputs.payload }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_tagged_quoted_run_key_is_not_invisible(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - !!str 'run': echo ${{ env.PAYLOAD }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_anchored_run_key_is_not_invisible(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - &chosen_run run: echo ${{ github.event.issue.body }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_explicit_tagged_run_key_is_not_invisible(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - ? !!str run\n    : echo ${{ secrets.TOKEN }}\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_tagged_escaped_run_key_is_not_invisible(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        encoded = chr(92) + "u0072un"
        head = self.commit('steps:\n  - !!str "' + encoded + '": echo ${{ vars.DATA }}\n')
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_tagged_unrelated_key_remains_outside_run_tripwire(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - !!str name: safe title\n    run: echo safe\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_unchanged_legacy_tagged_run_key_is_baseline_only(self):
        source = "steps:\n  - !!str run: echo safe\n"
        base = self.commit(source)
        head = self.commit(source)
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_legacy_tagged_run_key_anchor_edit_invalidates_waiver(self):
        source = "steps:\n  - !!str run: echo safe\n"
        base = self.commit(source)
        head = self.commit(source.replace("echo safe", "echo changed"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_new_risky_workflows_count_by_path_and_kind(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        extra = Path(".github/workflows/second.yml")
        extra.write_text("steps:\n  - run: echo ${{ inputs.OTHER }}\n")
        head = self.commit("steps:\n  - run: echo ${{ github.event.issue.title }}\n  - run: *shell\n")
        report = guard.audit(base, head)
        self.assertEqual(3, report["new_risks"])
        self.assertEqual([
            {"path": str(self.workflow), "risk_count": 2,
             "risk_kinds": [{"kind": "direct_interpolation", "count": 1},
                            {"kind": "yaml_structure_ambiguity", "count": 1}]},
            {"path": str(extra), "risk_count": 1,
             "risk_kinds": [{"kind": "direct_interpolation", "count": 1}]},
        ], report["new_risky_workflows"])
        self.assertEqual(sorted([str(self.workflow), str(extra)]),
                         report["affected_workflows"])
        self.assertNotIn("OTHER", json.dumps(report))
        self.assertNotIn("issue.title", json.dumps(report))

    def test_new_risky_workflows_empty_when_only_legacy_debt(self):
        legacy = "steps:\n  - run: echo ${{ github.event.issue.title }}\n"
        base = self.commit(legacy)
        head = self.commit(legacy + "# safe unrelated comment\n")
        report = guard.audit(base, head)
        self.assertEqual([], report["new_risky_workflows"])
        self.assertEqual([], report["new_risk_kinds"])
        self.assertEqual(0, report["new_risks"])

    def test_new_risky_workflows_distinguishes_legacy_from_delta(self):
        legacy = "steps:\n  - run: echo ${{ github.event.issue.title }}\n"
        base = self.commit(legacy)
        head = self.commit(legacy + "  - run: echo ${{ vars.NEW_SECRET }}\n")
        report = guard.audit(base, head)
        self.assertEqual(2, report["candidate_risks"])
        self.assertEqual(1, report["new_risks"])
        self.assertEqual([{"path": str(self.workflow), "risk_count": 1,
                           "risk_kinds": [{"kind": "direct_interpolation", "count": 1}]}],
                         report["new_risky_workflows"])

    def test_hosted_admission_checks_exact_git_report_identity(self):
        source = (MODULE.parents[1] / ".github/workflows/zcloud-shell-expression-ratchet-20261015.yml").read_text()
        for check in (
            "assert payload['schema'] == 'zcloud-shell-expression-ratchet-v1'",
            "assert payload['base_sha'] == os.environ['BASE_SHA']",
            "assert payload['head_sha'] == os.environ['EXPECTED_HEAD']",
            "assert payload['new_risk_kinds'] == []",
            "assert payload['new_risky_workflows'] == []",
            "assert payload['affected_workflows'] == []",
        ):
            with self.subTest(check=check):
                self.assertIn(check, source)

    def test_hosted_admission_checks_inventory_count_integrity(self):
        source = (MODULE.parents[1] / ".github/workflows/zcloud-shell-expression-ratchet-20261015.yml").read_text()
        self.assertIn("sum(item['risk_count'] for item in payload['baseline_risky_workflows'])",
                      source)
        self.assertIn("sum(item['risk_count'] for item in payload['candidate_risky_workflows'])",
                      source)
        self.assertIn("sum(item['count'] for item in payload['baseline_risk_kinds'])",
                      source)
        self.assertIn("sum(item['count'] for item in payload['candidate_risk_kinds'])",
                      source)

    def test_markdown_summary_is_readable_without_approval(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo harmless\n")
        summary = guard.markdown_summary(guard.audit(base, head))
        self.assertIn("Geen nieuwe risicovolle shellinvoegingen", summary)
        self.assertIn("Broncontrole, geen productievrijgave", summary)
        self.assertIn("0", summary)
        self.assertNotIn("release_authorized=true", summary)

    def test_markdown_summary_escapes_adversarial_workflow_names(self):
        payload = {"schema": "zcloud-shell-expression-ratchet-v1",
                   "baseline_risks": 1, "candidate_risks": 2, "new_risks": 1,
                   "new_risky_workflows": [
                       {"path": ".github/workflows/evil|<script>alert('x')</script>.yml",
                        "risk_count": 1,
                        "risk_kinds": [{"kind": "direct_interpolation", "count": 1}]}]}
        summary = guard.markdown_summary(payload)
        self.assertIn("&#124;", summary)
        self.assertIn("&lt;script&gt;", summary)
        self.assertNotIn("<script>", summary)
        self.assertNotIn("evil|", summary)
        self.assertIn("direct_interpolation", summary)

    def test_markdown_summary_bounds_large_file_lists(self):
        payload = {"schema": "zcloud-shell-expression-ratchet-v1",
                   "baseline_risks": 0, "candidate_risks": 25, "new_risks": 25,
                   "new_risky_workflows": [
                       {"path": f".github/workflows/fixture-{n:02d}.yml",
                        "risk_count": 1, "risk_kinds": []}
                       for n in range(25)]}
        summary = guard.markdown_summary(payload)
        self.assertIn("fixture-00", summary)
        self.assertNotIn("fixture-24", summary)
        self.assertIn("overige bestanden niet getoond", summary)
        self.assertLess(len(summary), 6000)

    def test_hosted_evidence_admission_renders_bounded_summary(self):
        workflow = (MODULE.parents[1] / ".github/workflows/zcloud-shell-expression-ratchet-20261015.yml").read_text()
        self.assertIn("from scripts.zcloud_shell_expression_ratchet_20261015 import markdown_summary", workflow)
        self.assertIn("os.environ.get('GITHUB_STEP_SUMMARY')", workflow)
        self.assertIn("summary.write(markdown_summary(payload))", workflow)

    def test_string_embedded_expression_closer_does_not_hide_github_event(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', github.event.issue.title) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_string_embedded_expression_closer_does_not_hide_inputs(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', inputs.payload) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_string_embedded_expression_closer_does_not_hide_step_outputs(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', steps.fetch.outputs.data) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_string_embedded_expression_closer_does_not_hide_secret(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', secrets.DEPLOY_TOKEN) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_string_embedded_expression_closer_with_doubled_quote(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('it''s }}', vars.COMMAND) }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_string_embedded_expression_closer_safe_constant_not_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', 'safe') }}\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_unterminated_expression_after_string_closer_is_denied(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', github.event.issue.body)\n")
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_multiple_expressions_with_embedded_closer_do_not_hide_second(self):
        base = self.commit("steps:\n  - run: echo safe\n")
        head = self.commit("steps:\n  - run: echo ${{ format('}}', 'safe') }} ${{ vars.NEW }}\n")
        self.assertEqual(1, guard.audit(base, head)["new_risks"])

    def test_legacy_shell_becomes_new_debt_when_root_permissions_escalate(self):
        old = "on: push\npermissions: read-all\njobs:\n  x:\n    steps:\n      - run: echo ${{ inputs.payload }}\n"
        base = self.commit(old)
        head = self.commit(old.replace("permissions: read-all", "permissions: write-all"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_shell_becomes_new_debt_when_event_trigger_escalates(self):
        old = "on: push\njobs:\n  x:\n    steps:\n      - run: echo ${{ github.event.issue.title }}\n"
        base = self.commit(old)
        head = self.commit(old.replace("on: push", "on: pull_request_target"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_shell_becomes_new_debt_when_job_permissions_escalate(self):
        old = ("jobs:\n  x:\n    permissions:\n      contents: read\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("contents: read", "contents: write"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_shell_becomes_new_debt_when_job_runner_changes(self):
        old = ("jobs:\n  x:\n    runs-on: ubuntu-latest\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("ubuntu-latest", "self-hosted"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_legacy_shell_becomes_new_debt_when_job_condition_relaxes(self):
        old = ("jobs:\n  x:\n    if: false\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("if: false", "if: true"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_unrelated_job_permissions_do_not_invalidate_existing_waiver(self):
        old = ("jobs:\n  x:\n    permissions:\n      contents: read\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n"
               "  unrelated:\n    permissions:\n      contents: read\n"
               "    steps:\n      - run: echo safe\n")
        base = self.commit(old)
        head = self.commit(old.replace(
            "  unrelated:\n    permissions:\n      contents: read",
            "  unrelated:\n    permissions:\n      contents: write"))
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_unrelated_workflow_title_does_not_invalidate_waiver(self):
        old = ("name: Original title\non: push\njobs:\n  x:\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("Original title", "Updated title"))
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_quoted_double_job_key_permissions_escalation_denied(self):
        old = ("jobs:\n  \"deploy\":\n    permissions:\n      contents: read\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("contents: read", "contents: write"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_quoted_single_job_key_runner_escalation_denied(self):
        old = ("jobs:\n  'deploy':\n    runs-on: ubuntu-latest\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("ubuntu-latest", "self-hosted"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_quoted_root_on_trigger_escalation_denied(self):
        old = ("'on': push\njobs:\n  deploy:\n"
               "    steps:\n      - run: echo ${{ github.event.issue.body }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("'on': push", "'on': pull_request_target"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_quoted_root_permissions_escalation_denied(self):
        old = ('"permissions": read-all\njobs:\n  deploy:\n'
               '    steps:\n      - run: echo ${{ inputs.payload }}\n')
        base = self.commit(old)
        head = self.commit(old.replace("permissions\": read-all", "permissions\": write-all"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_unrelated_quoted_job_control_change_preserves_legacy(self):
        old = ("jobs:\n  'danger':\n    runs-on: ubuntu-latest\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n"
               "  \"other\":\n    permissions: read-all\n"
               "    steps:\n      - run: echo safe\n")
        base = self.commit(old)
        head = self.commit(old.replace('  "other":\n    permissions: read-all',
                                      '  "other":\n    permissions: write-all'))
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_quoted_jobs_root_job_authority_change_denied(self):
        old = ("'jobs':\n  'deploy':\n    permissions: read-all\n"
               "    steps:\n      - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("permissions: read-all", "permissions: write-all"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_four_space_job_indent_permission_escalation_denied(self):
        old = ("jobs:\n    deploy:\n      permissions:\n        contents: read\n"
               "      steps:\n        - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("contents: read", "contents: write"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_four_space_job_indent_runner_escalation_denied(self):
        old = ("jobs:\n    deploy:\n      runs-on: ubuntu-latest\n"
               "      steps:\n        - run: echo ${{ inputs.payload }}\n")
        base = self.commit(old)
        head = self.commit(old.replace("ubuntu-latest", "self-hosted"))
        self.assertGreaterEqual(guard.audit(base, head)["new_risks"], 1)

    def test_four_space_unrelated_job_metadata_does_not_invalidate_waiver(self):
        old = ("jobs:\n    unsafe:\n      permissions: read-all\n"
               "      steps:\n        - run: echo ${{ inputs.payload }}\n"
               "    other:\n      permissions: read-all\n"
               "      steps:\n        - run: echo safe\n")
        base = self.commit(old)
        head = self.commit(old.replace("    other:\n      permissions: read-all",
                                       "    other:\n      permissions: write-all"))
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

    def test_boolean_all_output_authority_flags_false(self):
        base = self.commit("steps:\n  - run: echo x\n")
        head = self.commit("steps:\n  - run: echo y\n")
        report = guard.audit(base, head)
        self.assertEqual([False] * 3, [report[k] for k in
                         ("release_authorized", "deployment_authorized", "mutation_performed")])


if __name__ == "__main__":
    unittest.main()
