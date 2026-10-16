"""Offline regressions for new GitHub Actions workflow-name collisions."""
import importlib.util
import json
import sys
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

    def test_missing_name_uses_repository_relative_path(self):
        self.assertEqual(guard.workflow_name(".github/workflows/a.yml", b"on: push\n"), ".github/workflows/a.yml")

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
        self.assertEqual(guard.workflow_name(path, source), path)

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


    def test_git_read_enforces_output_budget_before_full_capture(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.check_call(["git", "init", "-q"], cwd=root)
            blob = subprocess.check_output(["git", "hash-object", "-w", "--stdin"],
                                           cwd=root, input=b"x" * 200000).decode().strip()
            with self.assertRaisesRegex(guard.InvalidInventory, "git_output_too_large"):
                guard._git(["cat-file", "blob", blob], root, 1024)

    def test_tree_object_cannot_impersonate_commit_in_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: Commit proof\n")
            git("add", ".")
            git("commit", "-qm", "baseline")
            tree = git("rev-parse", "HEAD^{tree}")
            with self.assertRaises(guard.InvalidInventory):
                guard.read_inventory(tree, cwd=root)


    def test_new_self_trigger_cycle_denied(self):
        names = {"a.yml": "Build"}
        report = guard.judge(names, names, {"a.yml": ()},
                             {"a.yml": ("Build",)})
        self.assertEqual(report["status"], "deny")
        self.assertEqual(report["added_cycle_edge_count"], 1)
        self.assertEqual(report["added_cycle_edges"],
                         [{"source": "a.yml", "target": "a.yml"}])
        self.assertFalse(report["merge_authorized"])

    def test_new_two_workflow_cycle_denied(self):
        names = {"a.yml": "Build", "b.yml": "Report"}
        base_refs = {"a.yml": ("Report",), "b.yml": ()}
        head_refs = {"a.yml": ("Report",), "b.yml": ("Build",)}
        report = guard.judge(names, names, base_refs, head_refs)
        self.assertEqual(report["added_cycle_edge_count"], 2)
        self.assertEqual(report["added_reference_risk_count"], 0)
        self.assertEqual(report["status"], "deny")

    def test_no_cycle_in_linear_chain(self):
        names = {"a.yml": "Build", "b.yml": "Report", "c.yml": "Verify"}
        refs = {"a.yml": ("Report",), "b.yml": ("Verify",), "c.yml": ()}
        self.assertEqual(guard.cyclic_reference_edges(names, refs), set())
        self.assertEqual(guard.judge(names, names, {}, refs)["status"], "pass")

    def test_three_step_cycle_and_existing_cycle_debt(self):
        names = {"a.yml": "A", "b.yml": "B", "c.yml": "C"}
        refs = {"a.yml": ("B",), "b.yml": ("C",), "c.yml": ("A",)}
        edges = guard.cyclic_reference_edges(names, refs)
        self.assertEqual(len(edges), 3)
        self.assertEqual(guard.judge(names, names, refs, refs)["status"], "pass")
        self.assertEqual(guard.judge(names, names, {}, refs)["added_cycle_edge_count"], 3)

    def test_existing_cycle_extension_is_new_debt(self):
        names = {"a.yml": "A", "b.yml": "B", "c.yml": "C"}
        baseline = {"a.yml": ("B",), "b.yml": ("A",), "c.yml": ()}
        expanded = {"a.yml": ("B", "C"), "b.yml": ("A",), "c.yml": ("B",)}
        report = guard.judge(names, names, baseline, expanded)
        self.assertEqual(report["added_cycle_edge_count"], 2)
        self.assertEqual(report["status"], "deny")

    def test_ambiguous_names_not_accidentally_resolved_as_cycle(self):
        names = {"a.yml": "Build", "b.yml": "Build", "c.yml": "Listener"}
        refs = {"a.yml": ("Listener",), "c.yml": ("Build",)}
        self.assertEqual(guard.cyclic_reference_edges(names, refs), set())
        report = guard.judge(names, names, {}, refs)
        self.assertEqual(report["added_reference_risk_count"], 1)
        self.assertEqual(report["added_cycle_edge_count"], 0)

    def test_real_git_commit_triggers_new_cycle_denial(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "one.yml").write_text(
                'name: One\non:\n  workflow_run:\n    workflows: [Two]\n')
            (folder / "two.yml").write_text("name: Two\non: push\n")
            git("add", ".")
            git("commit", "-qm", "baseline with linear edge")
            base = git("rev-parse", "HEAD")
            (folder / "two.yml").write_text(
                'name: Two\non:\n  workflow_run:\n    workflows: [One]\n')
            git("add", ".")
            git("commit", "-qm", "new cycle")
            head = git("rev-parse", "HEAD")
            bn, br = guard.read_inventory(base, cwd=root)
            hn, hr = guard.read_inventory(head, cwd=root)
            report = guard.judge(bn, hn, br, hr)
            self.assertEqual(report["added_collision_count"], 0)
            self.assertEqual(report["added_reference_risk_count"], 0)
            self.assertEqual(report["added_cycle_edge_count"], 2)
            self.assertEqual(report["status"], "deny")


    def test_unnamed_default_path_does_not_match_filename_only(self):
        path = ".github/workflows/build.yml"
        name = guard.workflow_name(path, b"on: push\n")
        self.assertEqual(name, path)
        risk = guard.reference_risks({path: name, "l.yml": "Listener"},
                                    {"l.yml": ("build.yml",)})
        self.assertIn(("l.yml", "missing_target", "build.yml"), risk)
        self.assertEqual(guard.reference_risks({path: name, "l.yml": "Listener"},
                                               {"l.yml": (path,)}), set())

    def test_documented_workflow_run_globs_require_independent_review(self):
        names = {"a.yml": "Build", "b.yml": "Build-staging", "listener.yml": "Listener"}
        for pattern in ("Build*", "Build?", "Build+", "!(Tests)", "Build[0-9]",
                        "Build{,-staging}"):
            with self.subTest(pattern=pattern):
                risk = guard.reference_risks(names, {"listener.yml": (pattern,)})
                self.assertIn(("listener.yml", "pattern_needs_review", pattern), risk)

    def test_explicitly_escaped_glob_characters_are_literal(self):
        names = {"a.yml": "Build*", "b.yml": "Listener"}
        self.assertEqual(guard.literal_workflow_filter(r"Build\*"), "Build*")
        self.assertEqual(guard.reference_risks(names, {"b.yml": (r"Build\*",)}), set())
        self.assertEqual(guard.cyclic_reference_edges(names, {"b.yml": (r"Build\*",)}), set())

    def test_new_unescaped_glob_pattern_denied_even_if_name_exists(self):
        names = {"a.yml": "Build*", "b.yml": "Listener"}
        verdict = guard.judge(names, names, {}, {"b.yml": ("Build*",)})
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["added_reference_risk_count"], 1)
        self.assertEqual(verdict["added_reference_risks"][0]["reason"], "pattern_needs_review")
        self.assertEqual(verdict["added_cycle_edge_count"], 0)

    def test_old_glob_debt_is_not_new_and_fix_is_allowed(self):
        names = {"a.yml": "Build", "b.yml": "Listener"}
        legacy = {"b.yml": ("Build*",)}
        exact = {"b.yml": ("Build",)}
        self.assertEqual(guard.judge(names, names, legacy, legacy)["status"], "pass")
        self.assertEqual(guard.judge(names, names, legacy, exact)["status"], "pass")
        self.assertEqual(guard.judge(names, names, exact, legacy)["status"], "deny")

    def test_unsupported_backslash_escape_and_trailing_escape_denied(self):
        self.assertIsNone(guard.literal_workflow_filter("Build" + chr(92)))
        self.assertIsNone(guard.literal_workflow_filter(r"Build\z"))
        self.assertEqual(guard.literal_workflow_filter("Build"), "Build")


    def test_unicode_nfc_equivalent_workflow_names_are_new_collision(self):
        base = {"a.yml": "Café"}
        candidate = {"a.yml": "Café", "b.yml": "Cafe\u0301"}
        verdict = guard.judge(base, candidate)
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["added_collision_count"], 1)
        self.assertEqual(verdict["added_collisions"][0]["paths"], ["a.yml", "b.yml"])
        self.assertFalse(verdict["merge_authorized"])

    def test_preexisting_unicode_normalization_debt_is_tolerated(self):
        names = {"a.yml": "Café", "b.yml": "Cafe\u0301"}
        self.assertEqual(guard.judge(names, names)["status"], "pass")
        self.assertEqual(guard.judge(names, {"a.yml": "Café"})["status"], "pass")


    def test_full_cli_denies_new_cycle_and_returns_nonzero_without_authority(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: A\non: push\n")
            (folder / "b.yml").write_text(
                "name: B\non:\n  workflow_run:\n    workflows: [A]\n")
            git("add", ".")
            git("commit", "-qm", "valid original")
            original = git("rev-parse", "HEAD")
            (folder / "a.yml").write_text(
                "name: A\non:\n  workflow_run:\n    workflows: [B]\n")
            git("add", ".")
            git("commit", "-qm", "introduce cycle")
            candidate = git("rev-parse", "HEAD")
            command = [sys.executable, str(SCRIPT), "--root", root,
                       "--base", original, "--head", candidate]
            failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(failed.returncode, 1, failed.stdout)
            result = json.loads(failed.stdout)
            self.assertEqual(result["status"], "deny")
            self.assertEqual(result["added_cycle_edge_count"], 2)
            self.assertIs(result["production_mutation_authorized"], False)
            self.assertIs(result["merge_authorized"], False)
            self.assertEqual(git("rev-parse", "HEAD"), candidate)
            # A repaired exact HEAD must return zero, but never release authority.
            (folder / "a.yml").write_text("name: A\non: push\n")
            git("add", ".")
            git("commit", "-qm", "repair cycle")
            fixed = git("rev-parse", "HEAD")
            passed = subprocess.run(command[:-1] + [fixed], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(passed.returncode, 0, passed.stdout)
            green = json.loads(passed.stdout)
            self.assertEqual(green["status"], "pass")
            self.assertIs(green["production_mutation_authorized"], False)
            self.assertIs(green["merge_authorized"], False)

    def test_full_cli_invalid_revision_fails_without_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            subprocess.check_call(["git", "init", "-q"], cwd=root)
            command = [sys.executable, str(SCRIPT), "--root", root,
                       "--base", "main", "--head", "a" * 40]
            denied = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(denied.returncode, 1)
            result = json.loads(denied.stdout)
            self.assertEqual(result["error_code"], "invalid_sha")
            self.assertIs(result["production_mutation_authorized"], False)
            self.assertNotIn("Traceback", denied.stderr)

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
