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

    def _parent_and_job_same_group(self):
        return document("shared", False).replace(
            "    runs-on: ubuntu-latest\n",
            "    concurrency:\n      group: shared\n"
            "      cancel-in-progress: false\n    runs-on: ubuntu-latest\n",
        )

    def test_new_parent_job_self_deadlock_is_denied(self):
        base = self.base(document("shared", False),
                         document("unrelated", False))
        self.write("first.yml", self._parent_and_job_same_group())
        self.denied(base, self.save())

    def test_existing_parent_job_deadlock_is_grandfathered_for_unrelated_edits(self):
        base = self.base(self._parent_and_job_same_group(),
                         document("unrelated", False))
        self.write("first.yml", self._parent_and_job_same_group()
                   + "# unrelated non-concurrency change\n")
        self.assertEqual(gate.check(base, self.save()), (1, 1))

    def test_self_deadlock_inventory_is_one_group_one_workflow(self):
        base = self.base(self._parent_and_job_same_group(),
                         document("unrelated", False))
        self.write("first.yml", self._parent_and_job_same_group()
                   + "# unrelated edit\n")
        receipt = gate.inventory(base, self.save())
        self.assertEqual(receipt["legacy_collision_count"], 1)
        self.assertEqual(receipt["legacy_group_count"], 1)
        self.assertEqual(receipt["unique_affected_workflows"], 1)
        self.assertEqual(receipt["group_summaries"][0]["unique_scopes"], 2)
        self.assertFalse(receipt["production_mutation_authorized"])

    def test_sibling_jobs_same_group_remain_allowed(self):
        base = self.base(document("shared", False, job=True),
                         document("unrelated", False))
        sibling = (
            "  sibling:\n"
            "    runs-on: ubuntu-latest\n"
            "    concurrency:\n"
            "      group: shared\n"
            "      cancel-in-progress: false\n"
            "    steps:\n"
            "      - run: echo ok\n"
        )
        self.write("first.yml", document("shared", False, job=True) + sibling)
        self.assertEqual(gate.check(base, self.save()), (0, 0))

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
        self.assertEqual(receipt["legacy_group_count"], 1)
        self.assertEqual(receipt["unique_affected_workflows"], 2)
        cluster = receipt["group_summaries"][0]
        self.assertEqual(cluster["pair_count"], 1)
        self.assertEqual(cluster["unique_workflow_count"], 2)
        self.assertFalse(cluster["reviewed"])
        self.assertFalse(cluster["production_mutation_authorized"])
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

    def test_grouped_inventory_counts_pairs_without_owner_approval(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("third.yml", document("shared", False))
        base = self.save()
        self.write("third.yml", document("shared", False, extra="# safe edit\n"))
        report = gate.inventory(base, self.save())
        self.assertEqual(report["legacy_collision_count"], 3)
        self.assertEqual(report["legacy_group_count"], 1)
        self.assertEqual(report["unique_affected_workflows"], 3)
        cluster = report["group_summaries"][0]
        self.assertEqual(cluster["pair_count"], 3)
        self.assertEqual(cluster["unique_workflow_count"], 3)
        self.assertEqual(cluster["unique_scopes"], 3)
        self.assertEqual(cluster["owner"], "UNASSIGNED")
        self.assertEqual(cluster["reviewer"], "UNASSIGNED")
        self.assertEqual(cluster["disposition"], "UNREVIEWED")
        self.assertFalse(report["production_mutation_authorized"])

    def test_group_summary_remains_sha_bound_across_safe_queue_migration(self):
        base = self.base(document("shared", False), document("shared", False))
        upgraded = document("shared", False).replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max")
        self.write("first.yml", upgraded)
        head = self.save()
        report = gate.inventory(base, head)
        self.assertEqual(report["head_commit_sha"], head)
        self.assertEqual(report["legacy_collision_count"], 1)
        self.assertEqual(report["group_summaries"][0]["pair_count"], 1)
        self.assertFalse(report["merge_authorized"])

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

    def test_new_opaque_format_expression_denied_without_static_proof(self):
        base = self.base()
        self.write("second.yaml", document("separate", False).replace(
            "group: separate", "group: \"${{ format('zcloud-{0}', github.ref) }}\""))
        self.denied(base, self.save())

    def test_new_opaque_fallback_expression_denied(self):
        base = self.base()
        self.write("second.yaml", document("separate", False).replace(
            "group: separate", "group: \"${{ github.head_ref || github.ref }}\""))
        self.denied(base, self.save())

    def test_new_incomplete_expression_denied(self):
        base = self.base()
        self.write("second.yaml", document("separate", False).replace(
            "group: separate", "group: \"prefix-${{ github.ref\""))
        self.denied(base, self.save())

    def test_legacy_opaque_expression_is_grandfathered(self):
        baseline = document("old", False).replace(
            "group: old", "group: \"${{ format('stable-{0}', github.ref) }}\"")
        base = self.base(baseline, document("separate", False))
        self.write("first.yml", baseline + "# unrelated text change\n")
        self.assertEqual(gate.check(base, self.save()), (0, 0))

    def test_rewriting_existing_opaque_expression_denied(self):
        baseline = document("old", False).replace(
            "group: old", "group: \"${{ format('stable-{0}', github.ref) }}\"")
        base = self.base(baseline, document("separate", False))
        revised = baseline.replace("stable-{0}", "updated-{0}")
        self.write("first.yml", revised)
        self.denied(base, self.save())

    def test_unchanged_opaque_expression_allows_queue_hardening(self):
        baseline = document("old", False).replace(
            "group: old", "group: \"${{ format('stable-{0}', github.ref) }}\"")
        base = self.base(baseline, document("separate", False))
        upgraded = baseline.replace(
            "cancel-in-progress: false", "cancel-in-progress: false\n  queue: max")
        self.write("first.yml", upgraded)
        self.assertEqual(gate.check(base, self.save()), (0, 0))

    def test_simple_dotted_expression_is_statically_admitted(self):
        base = self.base()
        self.write("second.yaml", document("separate", False).replace(
            "group: separate", "group: \"prefix-${{ github.ref }}\""))
        self.assertEqual(gate.check(base, self.save()), (0, 0))

    def test_opaque_inventory_never_claims_complete_static_coverage(self):
        baseline = document("old", False).replace(
            "group: old", "group: \"${{ format('stable-{0}', github.ref) }}\"")
        base = self.base(baseline, document("separate", False))
        self.write("first.yml", baseline + "# harmless edit\n")
        receipt = gate.inventory(base, self.save())
        self.assertEqual(receipt["legacy_opaque_expression_count"], 1)
        self.assertEqual(receipt["candidate_opaque_expression_count"], 1)
        self.assertFalse(receipt["static_expression_coverage_complete"])
        self.assertTrue(receipt["no_new_opaque_expressions"])
        self.assertEqual(len(receipt["unresolved_expression_reviews"]), 1)
        item = receipt["unresolved_expression_reviews"][0]
        self.assertEqual(item["workflow_path"], ".github/workflows/first.yml")
        self.assertEqual(item["disposition"], "UNREVIEWED")
        self.assertFalse(receipt["merge_authorized"])
        self.assertFalse(receipt["production_mutation_authorized"])
        self.assertNotIn("stable-", str(receipt))

    def test_existing_collision_cannot_gain_automatic_event(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("first.yml", document("shared", False).replace(
            "on: push", "on: [push, workflow_dispatch]"))
        self.denied(base, self.save())

    def test_existing_collision_cannot_remove_branch_event_filter(self):
        scoped = document("shared", False).replace(
            "on: push", "on:\n  push:\n    branches: [main]")
        base = self.base(scoped, document("shared", False))
        self.write("first.yml", document("shared", False))
        self.denied(base, self.save())

    def test_legacy_colliding_job_cannot_relax_if_guard(self):
        guarded = document("shared", False, job=True).replace(
            "  check:\n", "  check:\n    if: false\n")
        base = self.base(guarded, document("shared", False))
        self.write("first.yml", document("shared", False, job=True))
        self.denied(base, self.save())

    def test_changing_legacy_job_guard_requires_owner_review_even_if_narrower(self):
        original = document("shared", False, job=True)
        base = self.base(original, document("shared", False))
        self.write("first.yml", original.replace(
            "  check:\n", "  check:\n    if: false\n"))
        self.denied(base, self.save())

    def test_noncolliding_workflow_can_change_event_trigger(self):
        base = self.base(document("shared", False), document("separate", False))
        self.write("second.yaml", document("separate", False).replace(
            "on: push", "on: [push, workflow_dispatch]"))
        self.assertEqual(gate.check(base, self.save()), (0, 0))

    def test_removed_collision_allows_owner_event_cleanup(self):
        base = self.base(document("shared", False), document("shared", False))
        self.write("first.yml", document("separate", False).replace(
            "on: push", "on: workflow_dispatch"))
        self.assertEqual(gate.check(base, self.save()), (1, 0))

    def test_invalid_shas_denied(self):
        base = self.base()
        for invalid in ("main", "f" * 39, "z" * 40):
            self.denied(base, invalid)

if __name__ == "__main__":
    unittest.main()
