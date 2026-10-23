"""Offline adversarial tests for direct privileged cache write ratchet."""
import importlib.util
from pathlib import Path
from collections import Counter
from unittest import TestCase
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/zcloud_privileged_cache_write_ratchet_20261016.py"
spec = importlib.util.spec_from_file_location("cache_write_guard", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

PRIV = "on: [pull_request_target]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/cache@v4\n        with:\n          path: /tmp/node\n          key: pwnable\n"
SAFE = PRIV.replace("pull_request_target", "pull_request")
RESTORE = PRIV.replace("actions/cache@v4", "actions/cache/restore@v4")


class CacheWriteGuardTests(TestCase):
    def test_privileged_cache_is_found(self):
        self.assertEqual(sum(guard.cache_writers("a.yml", PRIV).values()), 1)

    def test_ordinary_pr_cache_does_not_trigger(self):
        self.assertEqual(guard.cache_writers("a.yml", SAFE), Counter())

    def test_restore_only_does_not_write(self):
        self.assertEqual(guard.cache_writers("a.yml", RESTORE), Counter())

    def test_save_subaction_is_writer(self):
        self.assertEqual(sum(guard.cache_writers("a.yml", PRIV.replace("actions/cache@v4", "actions/cache/save@v4")).values()), 1)

    def test_privileged_workflow_run_is_writer(self):
        self.assertEqual(sum(guard.cache_writers("a.yml", PRIV.replace("[pull_request_target]", "[workflow_run]")).values()), 1)

    def test_repository_dispatch_is_writer(self):
        self.assertEqual(sum(guard.cache_writers("a.yml", PRIV.replace("[pull_request_target]", "[repository_dispatch]")).values()), 1)

    def test_issue_comment_is_writer(self):
        self.assertEqual(sum(guard.cache_writers("a.yml", PRIV.replace("[pull_request_target]", "[issue_comment]")).values()), 1)

    def test_non_privileged_workflow_run_composite_absent(self):
        self.assertFalse(guard.cache_writers("a.yml", SAFE))

    def test_cache_restore_does_not_count_as_writer(self):
        self.assertFalse(guard.cache_writers("a.yml", RESTORE))

    def test_new_step_is_rejected(self):
        existing = guard.cache_writers("a.yml", PRIV)
        candidate = guard.cache_writers("a.yml", PRIV.replace("key: pwnable", "key: other"))
        self.assertEqual(len(guard.compare(existing, candidate)), 1)

    def test_step_shift_is_rejected(self):
        shifted = PRIV.replace("      - uses: actions/cache@v4", "      - run: echo before\n      - uses: actions/cache@v4")
        self.assertEqual(len(guard.compare(guard.cache_writers("a.yml", PRIV), guard.cache_writers("a.yml", shifted))), 1)

    def test_removal_is_allowed(self):
        self.assertFalse(guard.compare(guard.cache_writers("a.yml", PRIV), Counter()))

    def test_same_source_is_allowed(self):
        self.assertFalse(guard.compare(guard.cache_writers("a.yml", PRIV), guard.cache_writers("a.yml", PRIV)))

    def test_alias_used_in_uses_is_resolved(self):
        source = PRIV.replace("uses: actions/cache@v4", "uses: &cache_action actions/cache@v4\n      - uses: *cache_action")
        self.assertEqual(sum(guard.cache_writers("a.yml", source).values()), 2)

    def test_duplicate_on_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", PRIV + "\non: [push]\n")

    def test_duplicate_job_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", PRIV + "  build:\n    runs-on: ubuntu-latest\n")

    def test_unknown_trigger_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", "on: {pull_request_target: {}}\njobs: {}\non: {}\n")

    def test_no_trigger_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", "name: example\njobs: {}\n")

    def test_invalid_yaml_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", "on: [pull_request_target\njobs: {}\n")

    def test_privileged_invalid_step_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", "on: [pull_request_target]\njobs:\n  a:\n    steps: [null]\n")

    def test_privileged_unknown_with_type_is_denied(self):
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", PRIV.replace("with:\n          path: /tmp/node\n          key: pwnable", "with: nope"))

    def test_sha_validation(self):
        with self.assertRaises(guard.Denied):
            guard.verify_commit("not-a-commit")

    def test_tree_symlink_is_denied(self):
        record = b"120000 blob " + b"a" * 40 + b"\t.github/workflows/link.yml\0"
        with patch.object(guard, "run_git", return_value=record):
            with self.assertRaises(guard.Denied):
                guard.discover_workflows("a" * 40)

    def test_tree_gitlink_is_denied(self):
        record = b"160000 commit " + b"a" * 40 + b"\t.github/workflows/sub.yaml\0"
        with patch.object(guard, "run_git", return_value=record):
            with self.assertRaises(guard.Denied):
                guard.discover_workflows("a" * 40)

    def test_tree_duplicate_is_denied(self):
        record = b"100644 blob " + b"a" * 40 + b"\t.github/workflows/a.yml\0"
        def git(*args):
            return (record * 2 if args[0] == "ls-tree" else b"1" if args[:2] == ("cat-file", "-s") else b"x")
        with patch.object(guard, "run_git", side_effect=git):
            with self.assertRaises(guard.Denied):
                guard.discover_workflows("a" * 40)

    def test_foreign_origin_is_denied(self):
        with patch.object(guard, "run_git", return_value=b"git@github.com:other/zCloud.git\n"):
            with self.assertRaises(guard.Denied):
                guard.verify_current_main("a" * 40)

    def test_current_main_drift_is_denied(self):
        def git(*args):
            if args[0] == "remote":
                return b"https://github.com/Zennay/zCloud.git\n"
            return b"b" * 40 + b"\trefs/heads/main\n"
        with patch.object(guard, "run_git", side_effect=git):
            with self.assertRaises(guard.Denied):
                guard.verify_current_main("a" * 40)

    def test_current_main_matches_exactly(self):
        def git(*args):
            if args[0] == "remote":
                return b"https://github.com/Zennay/zCloud.git\n"
            return b"a" * 40 + b"\trefs/heads/main\n"
        with patch.object(guard, "run_git", side_effect=git):
            guard.verify_current_main("a" * 40)

    def test_counter_additions_not_canceled_by_deletions(self):
        first = Counter({"step-old": 1})
        second = Counter({"step-new": 1})
        self.assertEqual(guard.compare(first, second), [{"fingerprint": "step-new", "added": 1}])

    def test_no_mutation_grant_claim(self):
        self.assertFalse("deploy" in guard.__doc__.lower() and "authorized: true" in guard.__doc__.lower())


    def test_yaml_step_mapping_alias_is_resolved(self):
        source = ("on: [workflow_run]\njobs:\n  a:\n    steps:\n"
                  "      - &shared\n        uses: actions/cache/save@v4\n"
                  "        with: {path: /tmp, key: cache}\n      - *shared\n")
        self.assertEqual(sum(guard.cache_writers("a.yml", source).values()), 2)

    def test_privileged_step_merge_is_denied(self):
        source = ("on: [pull_request_target]\nshared: &step\n"
                  "  uses: actions/cache@v4\njobs:\n  x:\n"
                  "    steps:\n      - <<: *step\n")
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", source)

    def test_privileged_cache_input_merge_is_denied(self):
        source = ("on: [workflow_run]\nshared: &inputs {path: /tmp, key: abc}\n"
                  "jobs:\n  x:\n    steps:\n      - uses: actions/cache@v4\n"
                  "        with:\n          <<: *inputs\n")
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", source)

    def test_privileged_cyclic_alias_is_denied(self):
        source = ("on: [issue_comment]\nshared: &loop [*loop]\n"
                  "jobs:\n  x:\n    steps: []\n")
        with self.assertRaises(guard.Denied):
            guard.cache_writers("a.yml", source)

    def test_unprivileged_yaml_merge_remains_out_of_scope(self):
        source = ("on: [pull_request]\nshared: &step {uses: actions/cache@v4}\n"
                  "jobs:\n  x:\n    steps:\n      - <<: *step\n")
        self.assertFalse(guard.cache_writers("a.yml", source))

    def test_aliased_with_mapping_is_fingerprinted(self):
        source = ("on: [pull_request_target]\nshared: &inputs {path: /tmp, key: abc}\n"
                  "jobs:\n  x:\n    steps:\n      - uses: actions/cache@v4\n"
                  "        with: *inputs\n")
        self.assertEqual(sum(guard.cache_writers("a.yml", source).values()), 1)

    def test_deep_nested_yaml_is_denied(self):
        nested = {}
        for _ in range(66):
            nested = {"a": nested}
        with self.assertRaises(guard.Denied):
            guard.reject_ambiguous_merges(nested)

if __name__ == "__main__":
    import unittest
    unittest.main()
