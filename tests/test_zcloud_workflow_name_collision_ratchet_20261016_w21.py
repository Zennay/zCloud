"""Offline regressions for new GitHub Actions workflow-name collisions."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_workflow_name_collision_ratchet_20261016_w21.py"
spec = importlib.util.spec_from_file_location("workflow_name_collision_ratchet", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class NameCollisionRatchetTests(unittest.TestCase):
    def test_new_collision_is_denied(self):
        base = {"a.yml": "Build"}
        actual = guard.judge(base, {"a.yml": "Build", "b.yml": "Build"})
        self.assertEqual(actual["status"], "deny")
        self.assertEqual(actual["added_collision_count"], 1)
        self.assertFalse(actual["merge_authorized"])
        self.assertFalse(actual["production_mutation_authorized"])

    def test_legacy_collision_is_not_new_debt(self):
        base = {"a.yml": "Build", "b.yml": "Build"}
        self.assertEqual(guard.judge(base, dict(base))["status"], "pass")

    def test_remove_old_collision(self):
        base = {"a": "Build", "b": "Build"}
        self.assertEqual(guard.judge(base, {"a": "Build", "b": "Other"})["status"], "pass")

    def test_rename_and_changed_name_create_collision(self):
        base = {"a.yml": "Build", "b.yml": "Test"}
        self.assertEqual(guard.judge(base, {"a.yml": "Build", "b.yml": "Build"})["status"], "deny")
        self.assertEqual(guard.judge(base, {"a.yml": "Build", "c.yml": "Build"})["status"], "deny")

    def test_unique_new_name_is_safe_as_source_only(self):
        actual = guard.judge({"a": "A"}, {"a": "A", "b": "B"})
        self.assertEqual(actual["status"], "pass")
        self.assertFalse(actual["merge_authorized"])

    def test_yaml_quoted_names(self):
        path = ".github/workflows/a.yml"
        self.assertEqual(guard.workflow_name(path, b'name: "Build: fast" # comment\n'), "Build: fast")
        self.assertEqual(guard.workflow_name(path, b"name: 'Build: fast'\n"), "Build: fast")
        self.assertEqual(guard.workflow_name(path, b"name: 'CI''s check'\n"), "CI's check")

    def test_missing_name_uses_filename(self):
        self.assertEqual(guard.workflow_name(".github/workflows/a.yml", b"on: push\n"), "a.yml")

    def test_ambiguous_names_denied(self):
        invalid = (b"name: A\nname: B\n", b"name: \xff\n",
                   b"name: [foo]\n", b"name: ${{ github.ref }}\n",
                   b'name: "unterminated\n', b"name: Build: fast\n")
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(guard.InvalidInventory):
                guard.workflow_name(".github/workflows/a.yml", source)

    def test_yaml_quoted_name_keys_and_alias_shadow(self):
        path = ".github/workflows/a.yml"
        self.assertEqual(guard.workflow_name(path, b"'name': Build\n"), "Build")
        self.assertEqual(guard.workflow_name(path, b'"name": Build\n'), "Build")
        with self.assertRaises(guard.InvalidInventory):
            guard.workflow_name(path, b"'name': One\nname: Two\n")

    def test_giant_git_blob_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "oversized.yml").write_bytes(b"name: Too big\n" + b"x" * 132000)
            git("add", ".")
            git("commit", "-qm", "oversized")
            with self.assertRaisesRegex(guard.InvalidInventory, "workflow_blob_too_large"):
                guard.read_names(git("rev-parse", "HEAD"), cwd=root)

    def test_uniformly_indented_yaml_root_is_not_filename_fallback(self):
        path = ".github/workflows/runner.yml"
        actual = guard.workflow_name(path, b"  name: CI nested root\n  on: push\n")
        self.assertEqual(actual, "CI nested root")
        self.assertNotEqual(actual, "runner.yml")

    def test_nested_job_and_step_names_do_not_override_root(self):
        path = ".github/workflows/runner.yml"
        source = b"on: push\njobs:\n  test:\n    name: Job title\n    steps:\n      - name: Step title\n"
        self.assertEqual(guard.workflow_name(path, source), "runner.yml")

    def test_empty_or_flow_mapping_workflow_refused(self):
        for source in (b"", b"   # comment only\n", b'{"name": "Aliased CI"}\n'):
            with self.subTest(source=source), self.assertRaises(guard.InvalidInventory):
                guard.workflow_name(".github/workflows/a.yml", source)


    def test_new_workflow_run_reference_to_missing_target_denied(self):
        base_names = {"a.yml": "Build"}
        candidate_names = {"a.yml": "Build", "listener.yml": "Listener"}
        base_refs = {"a.yml": ()}
        candidate_refs = {"a.yml": (), "listener.yml": ("Missing",)}
        decision = guard.judge(base_names, candidate_names, base_refs, candidate_refs)
        self.assertEqual(decision["status"], "deny")
        self.assertEqual(decision["added_collision_count"], 0)
        self.assertEqual(decision["added_reference_risk_count"], 1)
        self.assertEqual(decision["added_reference_risks"][0]["reason"], "missing_target")

    def test_old_duplicate_target_with_new_listener_is_denied(self):
        base = {"a.yml": "Build", "b.yml": "Build"}
        candidate = dict(base, **{"listener.yml": "Listener"})
        verdict = guard.judge(base, candidate, {}, {"listener.yml": ("Build",)})
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["added_reference_risks"][0]["reason"], "ambiguous_target")

    def test_old_broken_listener_baseline_tolerated(self):
        names = {"source.yml": "Listener", "target.yml": "Target"}
        refs = {"source.yml": ("Unknown",)}
        self.assertEqual(guard.judge(names, names, refs, refs)["status"], "pass")
        self.assertEqual(guard.judge(names, names, refs, {"source.yml": ("Target",)})["status"], "pass")

    def test_removing_a_target_breaks_existing_workflow_run(self):
        base = {"listener.yml": "Listener", "target.yml": "Target"}
        target = {"listener.yml": "Listener"}
        verdict = guard.judge(base, target, {"listener.yml": ("Target",)},
                              {"listener.yml": ("Target",)})
        self.assertEqual(verdict["status"], "deny")

    def test_literal_workflow_run_formats(self):
        formats = (
            b'on:\n  workflow_run:\n    workflows: ["Build", "Lint"]\n    types: [completed]\n',
            b"on:\n  workflow_run:\n    workflows:\n      - Build\n      - Lint\n",
            b"  on:\n    workflow_run:\n      workflows: ['Build', 'Lint']\n",
            b'"on":\n  "workflow_run":\n    "workflows": [Build, Lint]\n',
        )
        for source in formats:
            with self.subTest(source=source):
                self.assertEqual(guard.workflow_run_targets(source), ("Build", "Lint"))

    def test_quoted_literal_workflow_run_target(self):
        source = b'on:\n  workflow_run:\n    workflows: ["Build, Test", "CI"]\n'
        self.assertEqual(guard.workflow_run_targets(source), ("Build, Test", "CI"))

    def test_unparsed_new_workflow_run_source_denied_without_release_authority(self):
        new_ref = b'on:\n  workflow_run:\n    workflows: \${{ github.event.workflow.name }}\n'
        self.assertIsNone(guard.workflow_run_targets(new_ref))
        verdict = guard.judge({}, {"new.yml": "New"}, {},
                              {"new.yml": guard.workflow_run_targets(new_ref)})
        self.assertEqual(verdict["status"], "deny")
        self.assertFalse(verdict["production_mutation_authorized"])

    def test_untrusted_invisible_workflow_name_rejected(self):
        path = ".github/workflows/a.yml"
        for name in ("hello\u202eworld", "hello\u200bworld", "hello\ud800world"):
            with self.subTest(name=name), self.assertRaises(guard.InvalidInventory):
                guard.workflow_name(path, ("name: " + name + "\n").encode("utf-8", "surrogatepass"))

    def test_genuine_git_reference_breakage_without_name_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "target.yml").write_text("name: Existing Build\non: push\n")
            git("add", ".")
            git("commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            (folder / "listener.yml").write_text(
                'name: Listener\non:\n  workflow_run:\n    workflows: ["Missing Build"]\n    types: [completed]\n')
            git("add", ".")
            git("commit", "-qm", "added invalid listener")
            head = git("rev-parse", "HEAD")
            bn, br = guard.read_inventory(base, cwd=root)
            cn, cr = guard.read_inventory(head, cwd=root)
            verdict = guard.judge(bn, cn, br, cr)
            self.assertEqual(verdict["added_collision_count"], 0)
            self.assertEqual(verdict["added_reference_risk_count"], 1)
            self.assertEqual(verdict["status"], "deny")

    def test_bad_revision_denied(self):
        with self.assertRaises(guard.InvalidInventory):
            guard.read_names("main", cwd=".")

    def test_real_git_graph_deny_and_repair(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: Stable CI\non: push\n")
            git("add", ".")
            git("commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            (folder / "b.yml").write_text("name: Stable CI\non: workflow_dispatch\n")
            git("add", ".")
            git("commit", "-qm", "collision")
            colliding = git("rev-parse", "HEAD")
            self.assertEqual(
                guard.judge(guard.read_names(base, cwd=root),
                            guard.read_names(colliding, cwd=root))["status"], "deny")
            (folder / "b.yml").write_text("name: Separate check\n")
            git("add", ".")
            git("commit", "-qm", "fixed")
            repaired = git("rev-parse", "HEAD")
            self.assertEqual(
                guard.judge(guard.read_names(base, cwd=root),
                            guard.read_names(repaired, cwd=root))["status"], "pass")

    def test_symlink_workflow_refused(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: Build\n")
            (folder / "b.yml").symlink_to("a.yml")
            git("add", ".")
            git("commit", "-qm", "symlink")
            with self.assertRaises(guard.InvalidInventory):
                guard.read_names(git("rev-parse", "HEAD"), cwd=root)


if __name__ == "__main__":
    unittest.main()
