"""Real-Git adversarial tests for the read-only concurrency collision ratchet."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_concurrency_group_collision_ratchet_20261016 as gate

def document(group="unique", cancel=False, *, job=False, extra=""):
    if isinstance(cancel, bool):
        setting = str(cancel).lower()
    else:
        setting = "'${{ github.event_name == ''push'' }}'"
    if group.startswith("${{"):
        group = f"'{group}'"
    entry = f"group: {group}\ncancel-in-progress: {setting}"
    if job:
        section = "jobs:\n  check:\n    runs-on: ubuntu-latest\n    concurrency:\n      " + entry.replace("\n", "\n      ") + "\n    steps:\n      - run: echo ok\n"
    else:
        section = "concurrency:\n  " + entry.replace("\n", "\n  ") + "\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"
    return "name: Test\non: push\n" + section + extra

class RatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prev = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.prev)
        for args in (("init", "-q"), ("config", "user.name", "ci"),
                     ("config", "user.email", "ci@example.invalid")):
            self.git(*args)

    def git(self, *args):
        return subprocess.check_output(("git", *args), stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, source):
        p = self.root / ".github" / "workflows" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source)

    def save(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def base(self, a=None, b=None):
        self.write("first.yml", a or document("shared", True))
        self.write("second.yaml", b or document("separate", False))
        return self.save()

    def denied(self, base, head):
        with self.assertRaises(gate.AdmissionError):
            gate.check(base, head)

    def test_unique_groups_remain_allowed(self):
        base = self.base()
        self.write("second.yaml", document("separate", True))
        self.assertEqual(gate.check(base, self.save()), (0, 0))

    def test_new_collision_denied_even_when_other_group_non_cancelling(self):
        base = self.base()
        self.write("second.yaml", document("shared", False))
        self.denied(base, self.save())

    def test_two_false_groups_still_replace_pending_runs(self):
        base = self.base(document("shared", False))
        self.write("second.yaml", document("shared", False))
        self.denied(base, self.save())

    def test_legacy_false_false_collision_remains_grandfathered(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("first.yml", document("shared", False, extra="# benign step edit\n"))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_queue_max_does_not_make_cross_workflow_collision_safe(self):
        base = self.base(document("shared", False))
        self.write("first.yml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.write("second.yaml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.denied(base, self.save())

    def test_queue_max_with_true_cancel_fails_closed(self):
        base = self.base()
        self.write("second.yaml", document("shared", True).replace(
            "cancel-in-progress: true", "cancel-in-progress: true\n  queue: max"))
        self.denied(base, self.save())

    def test_invalid_queue_setting_denied(self):
        base = self.base()
        self.write("second.yaml", document("separate", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: [max]"))
        self.denied(base, self.save())

    def test_legacy_single_to_max_is_monotonic_pending_loss_remediation(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("first.yml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_legacy_both_members_can_migrate_to_max(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("first.yml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.write("second.yaml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_max_to_single_legacy_escalation_denied(self):
        protected = document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max")
        base = self.base(protected, document("shared", False))
        self.write("first.yml", document("shared", False))
        self.denied(base, self.save())

    def test_true_to_false_is_monotonic_cancellation_remediation(self):
        base = self.base(document("shared", True), document("shared", False))
        self.write("first.yml", document("shared", False))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_true_to_false_and_single_to_max_can_coexist(self):
        base = self.base(document("shared", True), document("shared", False))
        self.write("first.yml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_dynamic_to_literal_false_is_monotonic(self):
        base = self.base(document("shared", "dynamic"), document("shared", False))
        self.write("first.yml", document("shared", False))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_dynamic_to_true_is_not_provably_safer(self):
        base = self.base(document("shared", "dynamic"), document("shared", False))
        self.write("first.yml", document("shared", True))
        self.denied(base, self.save())

    def test_mode_improvement_does_not_excuse_queue_regression(self):
        old_first = document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max")
        base = self.base(old_first, document("shared", False))
        self.write("first.yml", document("shared", True))
        self.denied(base, self.save())

    def test_new_member_with_max_queue_is_still_denied(self):
        base = self.base(document("shared", False), document("separate", False))
        self.write("second.yaml", document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max"))
        self.denied(base, self.save())

    def test_new_cancellation_on_existing_shared_group_denied(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("first.yml", document("shared", True))
        self.denied(base, self.save())

    def test_unchanged_legacy_risk_is_grandfathered(self):
        base = self.base(document("shared", True), document("shared", False))
        self.write("first.yml", document("shared", True, extra="# unrelated step-only change\n"))
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_legacy_cancel_escalation_denied(self):
        base = self.base(document("shared", True), document("shared", False))
        self.write("second.yaml", document("shared", True))
        self.denied(base, self.save())

    def test_legacy_third_member_growth_denied(self):
        base = self.base(document("shared", True), document("shared", False))
        self.write("third.yml", document("shared", False))
        self.denied(base, self.save())

    def test_job_and_workflow_groups_share_namespace(self):
        base = self.base()
        self.write("second.yaml", document("shared", False, job=True))
        self.denied(base, self.save())

    def test_group_names_case_insensitive(self):
        base = self.base()
        self.write("second.yaml", document("SHARED", False))
        self.denied(base, self.save())

    def test_dynamic_cancellation_conservatively_denied(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("second.yaml", document("shared", "dynamic"))
        self.denied(base, self.save())

    def test_unique_workflow_names_keep_namespaced_groups_separate(self):
        base = self.base()
        group = "${{ github.workflow }}-${{ github.ref }}"
        self.write("first.yml", document(group, True).replace("name: Test", "name: First"))
        self.write("second.yaml", document(group, True).replace("name: Test", "name: Second"))
        self.assertEqual(gate.check(base, self.save()), (0, 0))

    def test_duplicate_workflow_names_can_collide_despite_namespacing(self):
        base = self.base()
        group = "${{ github.workflow }}-${{ github.ref }}"
        self.write("first.yml", document(group, False))
        self.write("second.yaml", document(group, False))
        self.denied(base, self.save())

    def test_literal_group_aliases_workflow_name_expression(self):
        base = self.base()
        self.write("first.yml", document("${{ github.workflow }}", False))
        self.write("second.yaml", document("Test", False))
        self.denied(base, self.save())

    def test_simple_expression_whitespace_does_not_hide_collision(self):
        base = self.base()
        self.write("first.yml", document("prefix-${{github.ref}}", False))
        self.write("second.yaml", document("prefix-${{ github.ref }}", False))
        self.denied(base, self.save())

    def test_non_string_workflow_name_denied(self):
        base = self.base()
        self.write("second.yaml", document().replace("name: Test", "name: [Test]"))
        self.denied(base, self.save())

    def test_unsafe_symlink_workflow_denied(self):
        base = self.base()
        (self.root / ".github/workflows/third.yml").symlink_to("first.yml")
        self.denied(base, self.save())

    def test_duplicate_yaml_keys_denied(self):
        base = self.base()
        self.write("second.yaml", document() + "\nconcurrency: {}\n")
        self.denied(base, self.save())

    def test_malformed_group_denied(self):
        base = self.base()
        self.write("second.yaml", document("shared", False).replace("group: shared", "group: [shared]"))
        self.denied(base, self.save())

    def test_oversized_workflow_denied_before_parser(self):
        base = self.base()
        self.write("second.yaml", document() + "#" + "X" * (gate.MAX_BLOB_BYTES + 1))
        self.denied(base, self.save())

    def test_mismatched_head_denied(self):
        base = self.base()
        self.write("second.yaml", document("shared", False))
        head = self.save()
        self.denied(head, base)

    def test_non_descendant_denied(self):
        base = self.base()
        self.git("checkout", "-q", "--orphan", "unrelated")
        self.git("rm", "-q", "-rf", ".")
        self.write("third.yml", document())
        self.denied(base, self.save())

    def test_yaml_on_is_not_python_bool(self):
        parsed = gate.yaml.load(document().encode(), Loader=gate.StrictLoader)
        self.assertIn("on", parsed)
        self.assertNotIn(True, parsed)

    def test_deny_message_contains_paths_but_not_group(self):
        base = self.base()
        self.write("second.yaml", document("shared", False))
        try:
            gate.check(base, self.save())
        except gate.AdmissionError as e:
            self.assertIn("second.yaml", str(e))
            self.assertNotIn("shared", str(e))
        else:
            self.fail("collision was admitted")

    def test_untrusted_git_environment_is_ignored(self):
        from unittest.mock import patch
        base = self.base()
        self.write("second.yaml", document("separate", True))
        head = self.save()
        with patch.dict(os.environ, {
            "GIT_NAMESPACE": "attacker",
            "GIT_OBJECT_DIRECTORY": "/definitely/not/a/git/object/store",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/definitely/not/an/object/store",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.pager",
            "GIT_CONFIG_VALUE_0": "false",
        }):
            self.assertEqual(gate.check(base, head), (0, 0))

    def test_inventory_contains_sha_bound_reviewable_paths_without_authority(self):
        import json
        base = self.base(document("private-audit", False),
                         document("private-audit", False))
        self.write("first.yml", document("private-audit", False,
                                         extra="# harmless update\n"))
        head = self.save()
        receipt = gate.inventory(base, head)
        self.assertEqual(receipt["base_commit_sha"], base)
        self.assertEqual(receipt["head_commit_sha"], head)
        self.assertEqual(receipt["legacy_collision_count"], 1)
        self.assertEqual(receipt["new_collisions_allowed"], 0)
        self.assertFalse(receipt["reviewed"])
        self.assertFalse(receipt["merge_authorized"])
        self.assertFalse(receipt["production_mutation_authorized"])
        item = receipt["review_items"][0]
        self.assertEqual(item["owner"], "UNASSIGNED")
        self.assertEqual(item["reviewer"], "UNASSIGNED")
        self.assertEqual(item["disposition"], "UNREVIEWED")
        self.assertEqual({m["workflow_path"] for m in item["members"]},
                         {".github/workflows/first.yml", ".github/workflows/second.yaml"})
        self.assertEqual(len(item["group_sha256"]), 64)
        self.assertNotIn("private-audit", json.dumps(receipt))

    def test_inventory_refuses_new_collision_instead_of_approving(self):
        base = self.base(document("caution", False), document("separate", False))
        self.write("second.yaml", document("caution", False))
        with self.assertRaises(gate.AdmissionError):
            gate.inventory(base, self.save())

    def test_inventory_requires_exact_head(self):
        base = self.base()
        self.write("second.yaml", document("separate", True))
        head = self.save()
        with self.assertRaises(gate.AdmissionError):
            gate.inventory(head, base)

    def test_workflow_job_budget_denied_before_pairwise_analysis(self):
        base = self.base()
        jobs = "".join(
            "  job_%s:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: echo ok\n" % i
            for i in range(gate.MAX_JOBS_PER_WORKFLOW + 1)
        )
        self.write("second.yaml", "name: Excessive\non: push\njobs:\n" + jobs)
        self.denied(base, self.save())

    def test_repository_entry_budget_denied(self):
        from unittest.mock import patch
        base = self.base()
        self.write("second.yaml", document("new_group", True))
        head = self.save()
        with patch.object(gate, "MAX_GROUP_ENTRIES", 1):
            self.denied(base, head)

    def test_invalid_shas_denied(self):
        base = self.base()
        for invalid in ("main", "f" * 39, "z" * 40):
            self.denied(base, invalid)

if __name__ == "__main__":
    unittest.main()
