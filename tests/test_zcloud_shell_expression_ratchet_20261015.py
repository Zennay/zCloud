"""Real-Git and adversarial offline source checks. No external network required."""
import importlib.util
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

    def test_unchanged_flow_run_does_not_create_debt(self):
        source = "steps:\n  - {run: echo safe}\n"
        base = self.commit(source)
        head = self.commit(source + "# no new flow mappings\n")
        self.assertEqual(0, guard.audit(base, head)["new_risks"])

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

    def test_boolean_all_output_authority_flags_false(self):
        base = self.commit("steps:\n  - run: echo x\n")
        head = self.commit("steps:\n  - run: echo y\n")
        report = guard.audit(base, head)
        self.assertEqual([False] * 3, [report[k] for k in
                         ("release_authorized", "deployment_authorized", "mutation_performed")])


if __name__ == "__main__":
    unittest.main()
