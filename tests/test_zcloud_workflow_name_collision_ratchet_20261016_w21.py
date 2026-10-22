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


    def test_inline_trigger_alias_and_mapping_are_not_assumed_safe(self):
        unknown = (
            b"on: *shared_events\njobs: {}\n",
            b"on: &events push\njobs: {}\n",
            b"on: {push: {}, workflow_run: {workflows: [Test]}}\n",
            b"on: {push: {}}\n",
            b"on: [push, *shared_events]\n",
            b'on: "!custom"\n',
            b"on: workflow_run\n",
            b"on: [workflow_run]\n",
        )
        for source in unknown:
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_simple_inline_non_run_events_are_unambiguous(self):
        accepted = (
            b"on: push\n",
            b"on: [push, workflow_dispatch]\n",
            b'on: "pull_request"\n',
            b"on: 'release' # comment\n",
            b"on: [push, release] # comment\n",
            b"on: push # comment\n",
            b"on:\n  push:\n    branches: [main]\n",
        )
        for source in accepted:
            with self.subTest(source=source):
                self.assertEqual(guard.workflow_run_targets(source), ())

    def test_root_yaml_merge_and_complex_event_keys_not_assumed_absent(self):
        unknown = (
            b"<<: *event_config\nname: Build\njobs: {}\n",
            b"? on\n: {workflow_run: {workflows: [Build]}}\n",
            b"on:\n  <<: *events\njobs: {}\n",
            b"on:\n  push: {}\n  <<: *some_events\n",
            b"on:\n  ? workflow_run\n  : {workflows: [Build]}\n",
        )
        for source in unknown:
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_new_alias_event_field_is_new_debt_old_alias_is_baseline(self):
        alias_source = b"on: *triggers\n"
        self.assertIsNone(guard.workflow_run_targets(alias_source))
        original_names = {"a.yml": "Build"}
        candidate_names = {"a.yml": "Build", "new.yml": "New"}
        old_refs = {"a.yml": ()}
        new_refs = {"a.yml": (), "new.yml": guard.workflow_run_targets(alias_source)}
        decision = guard.judge(original_names, candidate_names, old_refs, new_refs)
        self.assertEqual(decision["status"], "deny")
        self.assertEqual(decision["added_reference_risks"][0]["reason"], "unknown_source")
        self.assertEqual(guard.judge(candidate_names, candidate_names, new_refs, new_refs)["status"],
                         "pass")
        self.assertFalse(decision["production_mutation_authorized"])

    def test_inline_event_supported_and_unsupported_quoted(self):
        self.assertEqual(guard.workflow_run_targets(b'on: ["push", "release"]\n'), ())
        self.assertIsNone(guard.workflow_run_targets(b'on: "!strange"\n'))


    def test_workflow_run_inline_target_list_accepts_trailing_yaml_comments(self):
        sources = (
            b'on:\n  workflow_run:\n    workflows: [Build, Lint] # ordinary comment\n',
            b'on:\n  workflow_run:\n    workflows: ["Build", "Lint"] # explanation\n',
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(guard.workflow_run_targets(source), ("Build", "Lint"))

    def test_duplicate_workflow_run_keys_not_accepted_as_unambiguous(self):
        source = (
            b"on:\n"
            b"  workflow_run:\n"
            b"    workflows: [Build]\n"
            b"  workflow_run:\n"
            b"    workflows: [Other]\n"
        )
        self.assertIsNone(guard.workflow_run_targets(source))
        first = {"build.yml": "Build"}
        candidate = dict(first, **{"listener.yml": "Listener"})
        outcome = guard.judge(first, candidate, {}, {
            "listener.yml": guard.workflow_run_targets(source)})
        self.assertEqual(outcome["status"], "deny")
        self.assertEqual(outcome["added_reference_risks"][0]["reason"], "unknown_source")

    def test_inline_comment_cannot_hide_extra_flow_list_data(self):
        sources = (
            b"on:\n  workflow_run:\n    workflows: [Build] garbage\n",
            b"on:\n  workflow_run:\n    workflows: [Build]] garbage\n",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_simple_flow_list_workflow_trigger_comment_is_supported(self):
        self.assertEqual(
            guard.workflow_run_targets(b"on: [push, workflow_dispatch] # comment\n"), ())


    def test_escaped_yaml_on_root_key_is_not_silently_skipped(self):
        sources = (
            b'"o\\u006e":\n  workflow_run:\n    workflows: [Build]\n',
            b'"\\u006fn":\n  workflow_run:\n    workflows: [Build]\n',
            b'"o\\x6e":\n  workflow_run:\n    workflows: [Build]\n',
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_escaped_yaml_name_root_key_is_not_filename_fallback(self):
        path = ".github/workflows/build.yml"
        for escaped in (
            b'"n\\u0061me": Build\non: push\n',
            b'"na\\x6de": Build\non: push\n',
        ):
            with self.subTest(source=escaped), self.assertRaisesRegex(
                    guard.InvalidInventory, "ambiguous_escaped_root_key"):
                guard.workflow_name(path, escaped)

    def test_ordinary_quoted_root_keys_and_escaped_values_continue_working(self):
        path = ".github/workflows/build.yml"
        valid = b'"name": "Build: \\u2713"\n"on": [push, workflow_dispatch]\n'
        self.assertEqual(guard.workflow_name(path, valid), "Build: \u2713")
        self.assertEqual(guard.workflow_run_targets(valid), ())

    def test_value_backslash_not_mistaken_for_escaped_mapping_key(self):
        source = b'name: "Build \\\\ logs"\non: push\n'
        self.assertEqual(guard.workflow_run_targets(source), ())


    def test_yaml_merge_synthesized_workflow_name_rejected(self):
        path = ".github/workflows/name.yml"
        sources = (
            b"common: &defaults\n  name: Hidden name\n<<: *defaults\non: push\n",
            b"? name\n: Hidden name\non: push\n",
            b"on: push\n<<: *defaults\n",
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaisesRegex(
                    guard.InvalidInventory, "ambiguous_workflow_name_root"):
                guard.workflow_name(path, source)

    def test_yaml_root_scan_does_not_misread_nested_yaml_merges(self):
        source = b"name: Literal\njobs:\n  test:\n    <<: *job_template\non: push\n"
        self.assertEqual(guard.workflow_name(".github/workflows/a.yml", source), "Literal")


    def test_escaped_yaml_event_key_cannot_hide_workflow_run_trigger(self):
        sources = (
            b'on:\n  "workflow\\u005frun":\n    workflows: [Build]\n',
            b'on:\n  "work\\x66low_run":\n    workflows: [Build]\n',
            b'on:\n  workflow_run:\n    workflows: [Build]\n  "workflow\\u005frun":\n    workflows: [Other]\n',
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_escaped_workflows_field_cannot_shadow_plain_value(self):
        sources = (
            b'on:\n  workflow_run:\n    "work\\u0066lows": [Build]\n',
            b'on:\n  workflow_run:\n    workflows: [Build]\n    "work\\u0066lows": [Other]\n',
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_literal_nested_yaml_key_in_quotes_still_supported(self):
        source = b'"on":\n  "workflow_run":\n    "workflows": ["Build"]\n'
        self.assertEqual(guard.workflow_run_targets(source), ("Build",))


    def test_unchanged_unparsed_trigger_blob_is_grandfathered(self):
        names = {"a.yml": "A"}
        refs = {"a.yml": None}
        unchanged = {"a.yml": "1" * 40}
        actual = guard.judge(names, names, refs, refs,
                             base_blobs=unchanged, candidate_blobs=unchanged)
        self.assertEqual(actual["status"], "pass")
        self.assertEqual(actual["added_reference_risk_count"], 0)
        self.assertFalse(actual["production_mutation_authorized"])

    def test_rewritten_legacy_unparsed_trigger_blob_is_new_debt(self):
        names = {"a.yml": "A"}
        refs = {"a.yml": None}
        actual = guard.judge(names, names, refs, refs,
                             base_blobs={"a.yml": "1" * 40},
                             candidate_blobs={"a.yml": "2" * 40})
        self.assertEqual(actual["status"], "deny")
        self.assertEqual(actual["added_reference_risk_count"], 1)
        self.assertEqual(actual["added_reference_risks"][0]["reason"],
                         "unknown_source_changed")
        self.assertFalse(actual["merge_authorized"])

    def test_parseable_repair_of_unknown_is_allowed(self):
        names = {"a.yml": "A", "b.yml": "B"}
        old_refs = {"a.yml": None}
        repaired_refs = {"a.yml": ("B",)}
        actual = guard.judge(
            names, names, old_refs, repaired_refs,
            base_blobs={"a.yml": "1" * 40},
            candidate_blobs={"a.yml": "2" * 40})
        self.assertEqual(actual["status"], "pass")
        self.assertEqual(actual["added_reference_risk_count"], 0)

    def test_new_workflow_does_not_disturb_unchanged_legacy_unknown_source(self):
        base = {"a.yml": "A"}
        next_names = {"a.yml": "A", "b.yml": "B"}
        actual = guard.judge(
            base, next_names, {"a.yml": None},
            {"a.yml": None, "b.yml": ()},
            base_blobs={"a.yml": "1" * 40},
            candidate_blobs={"a.yml": "1" * 40, "b.yml": "2" * 40})
        self.assertEqual(actual["status"], "pass")

    def test_real_git_cli_denies_changed_legacy_unknown_source(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: A\non: *events_one\n")
            git("add", ".")
            git("commit", "-qm", "legacy unparsed baseline")
            baseline = git("rev-parse", "HEAD")
            (folder / "a.yml").write_text("name: A\non: *events_two\n")
            git("add", ".")
            git("commit", "-qm", "mutated unparsed trigger")
            modified = git("rev-parse", "HEAD")
            bn, br, bb = guard.read_inventory(baseline, cwd=root, include_blob_ids=True)
            hn, hr, hb = guard.read_inventory(modified, cwd=root, include_blob_ids=True)
            result = guard.judge(bn, hn, br, hr, base_blobs=bb, candidate_blobs=hb)
            self.assertEqual(result["status"], "deny")
            self.assertEqual(result["added_reference_risk_count"], 1)
            cli = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", root,
                 "--base", baseline, "--head", modified],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(cli.returncode, 1, cli.stdout)
            self.assertEqual(
                json.loads(cli.stdout)["added_reference_risks"][0]["reason"],
                "unknown_source_changed")


    def test_yaml_flow_comment_brackets_do_not_change_target(self):
        source = b'on:\n  workflow_run:\n    workflows: [Build] # comment includes ] and [\n'
        self.assertEqual(guard.workflow_run_targets(source), ("Build",))
        self.assertEqual(
            guard.workflow_run_targets(b"on: [push, release] # comment with ]\n"), ())

    def test_brackets_inside_quoted_flow_item_are_not_collection_end(self):
        source = b'on:\n  workflow_run:\n    workflows: ["Build] quality", "CI"] # check ]\n'
        self.assertEqual(guard.workflow_run_targets(source), ("Build] quality", "CI"))
        self.assertEqual(
            guard.workflow_run_targets(
                b"on:\n  workflow_run:\n    workflows: ['CI''s [Build]', Lint]\n"),
            ("CI's [Build]", "Lint"))

    def test_extra_unquoted_closing_delimiter_is_rejected(self):
        for source in (
            b"on:\n  workflow_run:\n    workflows: [Build]]\n",
            b"on:\n  workflow_run:\n    workflows: [Build], Lint\n",
            b"on:\n  workflow_run:\n    workflows: [Build, [Nested]]\n",
        ):
            with self.subTest(source=source):
                self.assertIsNone(guard.workflow_run_targets(source))

    def test_quoted_flow_values_with_escaped_quotes(self):
        source = b'on:\n  workflow_run:\n    workflows: ["Build \\"Done\\"", CI]\n'
        self.assertEqual(guard.workflow_run_targets(source), ('Build "Done"', "CI"))


    def test_new_workflow_matching_legacy_glob_is_not_silent(self):
        baseline = {"build.yml": "Build", "watch.yml": "Watcher"}
        refs = {"watch.yml": ("Build*",)}
        candidate = dict(baseline, **{"build_extra.yml": "Build extra"})
        result = guard.judge(baseline, candidate, refs, refs)
        self.assertEqual(result["status"], "deny")
        self.assertEqual(result["added_reference_risk_count"], 1)
        self.assertEqual(result["added_reference_risks"][0]["reason"],
                         "legacy_pattern_new_target")
        self.assertEqual(result["added_reference_risks"][0]["target"],
                         "build_extra.yml")
        self.assertFalse(result["merge_authorized"])

    def test_nonmatching_simple_legacy_glob_is_allowed(self):
        baseline = {"build.yml": "Build", "watch.yml": "Watcher"}
        refs = {"watch.yml": ("Build*",)}
        candidate = dict(baseline, **{"docs.yml": "Documentation"})
        result = guard.judge(baseline, candidate, refs, refs)
        self.assertEqual(result["added_reference_risk_count"], 0)
        self.assertEqual(result["status"], "pass")

    def test_extended_legacy_glob_requires_review_of_name_addition(self):
        baseline = {"build.yml": "Build", "watch.yml": "Watcher"}
        refs = {"watch.yml": ("!(Tests)",)}
        candidate = dict(baseline, **{"docs.yml": "Documentation"})
        result = guard.judge(baseline, candidate, refs, refs)
        self.assertEqual(result["status"], "deny")
        self.assertEqual(result["added_reference_risks"][0]["reason"],
                         "legacy_pattern_new_target")

    def test_missing_target_becoming_real_is_a_new_trigger(self):
        baseline = {"watch.yml": "Watcher"}
        refs = {"watch.yml": ("Ghost",)}
        candidate = {"watch.yml": "Watcher", "new.yml": "Ghost"}
        result = guard.judge(baseline, candidate, refs, refs)
        self.assertEqual(result["status"], "deny")
        self.assertEqual(result["added_reference_risk_count"], 1)
        self.assertEqual(result["added_reference_risks"][0]["reason"],
                         "missing_target_activated")

    def test_old_broken_target_can_be_repaired_by_changing_its_reference(self):
        baseline = {"watch.yml": "Watcher", "build.yml": "Build"}
        candidate = dict(baseline, **{"new.yml": "Ghost"})
        before = {"watch.yml": ("Ghost",)}
        after = {"watch.yml": ("Build",)}
        result = guard.judge(baseline, candidate, before, after)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["added_reference_risk_count"], 0)

    def test_unchanged_legacy_pattern_debt_is_still_grandfathered(self):
        names = {"watch.yml": "Watcher", "build.yml": "Build"}
        refs = {"watch.yml": ("Build*",)}
        result = guard.judge(names, names, refs, refs)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["added_reference_risk_count"], 0)
        self.assertFalse(result["production_mutation_authorized"])

    def test_real_git_cli_detects_activated_missing_workflow_run_target(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "watch.yml").write_text(
                'name: Watcher\non:\n  workflow_run:\n    workflows: ["Ghost"]\n')
            git("add", ".")
            git("commit", "-qm", "missing target in baseline")
            baseline = git("rev-parse", "HEAD")
            (folder / "new.yml").write_text("name: Ghost\non: push\n")
            git("add", ".")
            git("commit", "-qm", "suddenly live trigger")
            head = git("rev-parse", "HEAD")
            command = [sys.executable, str(SCRIPT), "--base", baseline,
                       "--head", head, "--root", root]
            denied = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(denied.returncode, 1, denied.stdout)
            verdict = json.loads(denied.stdout)
            self.assertEqual(verdict["status"], "deny")
            self.assertEqual(verdict["added_reference_risks"][0]["reason"],
                             "missing_target_activated")
            self.assertIs(verdict["production_mutation_authorized"], False)


    def test_unresolved_historical_debt_is_visible_even_when_passed(self):
        names = {"a.yml": "Build", "b.yml": "Build", "watch.yml": "Watcher"}
        references = {"a.yml": None, "watch.yml": ("Build", "Ghost")}
        verdict = guard.judge(names, names, references, references)
        self.assertEqual(verdict["status"], "pass")
        self.assertEqual(verdict["baseline_collision_count"], 1)
        self.assertEqual(verdict["baseline_reference_risk_count"], 3)
        self.assertEqual(verdict["candidate_reference_risk_count"], 3)
        self.assertEqual(verdict["added_reference_risk_count"], 0)
        self.assertEqual(verdict["baseline_cycle_edge_count"], 0)
        self.assertFalse(verdict["production_mutation_authorized"])

    def test_reference_baseline_preview_is_bounded_and_deterministic(self):
        names = {"watch.yml": "Watcher"}
        references = {"watch.yml": tuple("Missing %03d" % x for x in range(40))}
        outcome = guard.judge(names, names, references, references)
        self.assertEqual(outcome["baseline_reference_risk_count"], 40)
        self.assertEqual(len(outcome["baseline_reference_risks"]), 10)
        self.assertEqual(outcome["baseline_reference_risks"][0]["target"], "Missing 000")
        self.assertEqual(outcome["baseline_reference_risks"][-1]["target"], "Missing 009")
        self.assertEqual(outcome["status"], "pass")

    def test_existing_cycle_is_explicit_in_pass_evidence(self):
        names = {"a.yml": "A", "b.yml": "B"}
        refs = {"a.yml": ("B",), "b.yml": ("A",)}
        status = guard.judge(names, names, refs, refs)
        self.assertEqual(status["status"], "pass")
        self.assertEqual(status["baseline_cycle_edge_count"], 2)
        self.assertEqual(status["candidate_cycle_edge_count"], 2)
        self.assertEqual(status["added_cycle_edge_count"], 0)

    def test_real_git_cli_receipt_binds_both_exact_commit_shas(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "build.yml").write_text("name: Build\non: push\n")
            git("add", ".")
            git("commit", "-qm", "baseline")
            baseline = git("rev-parse", "HEAD")
            (folder / "additional.yml").write_text("name: Additional\non: push\n")
            git("add", ".")
            git("commit", "-qm", "unique new workflow")
            candidate = git("rev-parse", "HEAD")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", root,
                 "--base", baseline, "--head", candidate],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["source_commit_base"], baseline)
            self.assertEqual(receipt["source_commit_head"], candidate)
            self.assertEqual(receipt["workflow_count_base"], 1)
            self.assertEqual(receipt["workflow_count_head"], 2)
            self.assertEqual(receipt["baseline_reference_risk_count"], 0)
            self.assertEqual(receipt["candidate_reference_risk_count"], 0)
            self.assertFalse(receipt["merge_authorized"])


    def test_real_git_cli_legacy_wildcard_new_matching_workflow_denied(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "watch.yml").write_text(
                'name: Watcher\non:\n  workflow_run:\n    workflows: ["Build*"]\n')
            (folder / "build.yml").write_text("name: Build\non: push\n")
            git("add", ".")
            git("commit", "-qm", "legacy wildcard trigger")
            baseline = git("rev-parse", "HEAD")
            (folder / "docs.yml").write_text("name: Documentation\non: push\n")
            git("add", ".")
            git("commit", "-qm", "unrelated new workflow")
            unrelated = git("rev-parse", "HEAD")
            command = [sys.executable, str(SCRIPT), "--root", root,
                       "--base", baseline, "--head", unrelated]
            safe = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(safe.returncode, 0, safe.stdout)
            self.assertEqual(json.loads(safe.stdout)["status"], "pass")
            (folder / "build_extra.yml").write_text("name: Build extra\non: push\n")
            git("add", ".")
            git("commit", "-qm", "newly matched wildcard target")
            risky = git("rev-parse", "HEAD")
            command[-1] = risky
            denied = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(denied.returncode, 1, denied.stdout)
            receipt = json.loads(denied.stdout)
            self.assertEqual(receipt["source_commit_head"], risky)
            self.assertEqual(receipt["added_reference_risks"][0]["reason"],
                             "legacy_pattern_new_target")
            self.assertIs(receipt["merge_authorized"], False)
            self.assertIs(receipt["production_mutation_authorized"], False)

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
