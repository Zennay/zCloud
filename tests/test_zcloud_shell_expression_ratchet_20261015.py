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

    def test_boolean_all_output_authority_flags_false(self):
        base = self.commit("steps:\n  - run: echo x\n")
        head = self.commit("steps:\n  - run: echo y\n")
        report = guard.audit(base, head)
        self.assertEqual([False] * 3, [report[k] for k in
                         ("release_authorized", "deployment_authorized", "mutation_performed")])


if __name__ == "__main__":
    unittest.main()
