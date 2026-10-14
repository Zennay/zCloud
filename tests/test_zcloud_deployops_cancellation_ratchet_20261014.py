"""Offline Git cancellation policy regression suite. Does not touch VPS."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts/zcloud_deployops_cancellation_ratchet_20261014.py"
spec = importlib.util.spec_from_file_location("zcloud_cancellation", MODULE)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)

SAFE = """name: safe
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: isolated
  cancel-in-progress: false
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: echo safe
"""
SELFHOST = SAFE.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted, zcloud, vps]")
CANCEL = SELFHOST.replace("cancel-in-progress: false", "cancel-in-progress: true")
DYNAMIC = CANCEL.replace("runs-on: [self-hosted, zcloud, vps]",
                         "runs-on: " + "$" + "{{ fromJSON(needs.detect.outputs.runner) }}")
JOB_CANCEL = SAFE.replace("  cancel-in-progress: false\n", "").replace(
    "    runs-on: ubuntu-latest",
    "    concurrency:\n      group: shared-live-service\n"
    "      cancel-in-progress: true\n    runs-on: [self-hosted, zcloud, vps]"
)


class PolicyTest(unittest.TestCase):
    def test_hosted_global_cancel_is_safe(self):
        hosted = SAFE.replace("cancel-in-progress: false", "cancel-in-progress: true")
        self.assertEqual([], scanner.risks(hosted))

    def test_selfhost_global_cancel_denied(self):
        self.assertTrue(scanner.risks(CANCEL))

    def test_manual_selfhost_global_cancel_denied(self):
        self.assertTrue(scanner.risks(CANCEL.replace("pull_request:", "workflow_dispatch:")))

    def test_non_cancelling_selfhost_allowed(self):
        self.assertEqual([], scanner.risks(SELFHOST))

    def test_global_concurrency_after_jobs_is_checked(self):
        variant = SELFHOST.replace(
            "concurrency:\n  group: isolated\n  cancel-in-progress: false\n", ""
        ) + "\nconcurrency:\n  group: shared\n  cancel-in-progress: true\n"
        self.assertTrue(scanner.risks(variant))

    def test_inline_global_cancel_refused(self):
        variant = SELFHOST.replace(
            "concurrency:\n  group: isolated\n  cancel-in-progress: false",
            "concurrency: {group: shared, cancel-in-progress: true}",
        )
        self.assertTrue(scanner.risks(variant))

    def test_inline_job_cancel_refused(self):
        variant = SELFHOST.replace(
            "    runs-on: [self-hosted, zcloud, vps]",
            "    concurrency: {group: shared, cancel-in-progress: true}\n"
            "    runs-on: [self-hosted, zcloud, vps]",
        )
        self.assertTrue(scanner.risks(variant))

    def test_comment_does_not_enable_cancellation(self):
        variant = SELFHOST + "\n# cancel-in-progress: true\n"
        self.assertEqual([], scanner.risks(variant))

    def test_quoted_true_denied(self):
        self.assertTrue(scanner.risks(
            CANCEL.replace("cancel-in-progress: true", "cancel-in-progress: 'true'")
        ))

    def test_quoted_false_is_not_a_boolean_proof(self):
        for quoted in ("'false'", '"false"'):
            with self.subTest(quoted=quoted):
                example = SELFHOST.replace(
                    "cancel-in-progress: false",
                    "cancel-in-progress: " + quoted,
                )
                self.assertTrue(scanner.risks(example))

    def test_literal_false_remains_non_cancelling(self):
        self.assertEqual([], scanner.risks(SELFHOST))

    def test_expression_denied(self):
        self.assertTrue(scanner.risks(CANCEL.replace(
            "cancel-in-progress: true",
            "cancel-in-progress: " + "$" + "{{ github.ref == 'refs/heads/main' }}"
        )))

    def test_private_hosted_label_spoof_refused(self):
        self.assertTrue(scanner.risks(CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: ubuntu-private-vps"
        )))

    def test_nonstandard_global_indent_is_denied(self):
        text = CANCEL.replace("  cancel-in-progress: true",
                              "   cancel-in-progress: true")
        self.assertRaises(scanner.Unverifiable, scanner.risks, text)

    def test_nonstandard_job_indent_is_denied(self):
        text = JOB_CANCEL.replace("      cancel-in-progress: true",
                                  "       cancel-in-progress: true")
        self.assertRaises(scanner.Unverifiable, scanner.risks, text)

    def test_global_cancel_with_space_before_colon_is_denied(self):
        for whitespace in (" ", "  ", "\t"):
            with self.subTest(whitespace=repr(whitespace)):
                changed = CANCEL.replace(
                    "cancel-in-progress: true",
                    "cancel-in-progress" + whitespace + ": true"
                )
                self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_concurrency_with_space_before_colon_is_denied(self):
        for whitespace in (" ", "\t"):
            with self.subTest(whitespace=repr(whitespace)):
                changed = CANCEL.replace(
                    "concurrency:", "concurrency" + whitespace + ":"
                )
                self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_job_cancel_with_space_before_colon_is_denied(self):
        changed = JOB_CANCEL.replace(
            "cancel-in-progress: true", "cancel-in-progress : true"
        )
        self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_duplicate_global_cancel_even_false_is_denied(self):
        changed = SELFHOST.replace(
            "  cancel-in-progress: false",
            "  cancel-in-progress: false\n  cancel-in-progress: false"
        )
        self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_duplicate_global_concurrency_even_safe_is_denied(self):
        changed = SAFE.replace(
            "jobs:\n",
            "concurrency:\n  group: different\n  cancel-in-progress: false\njobs:\n"
        )
        self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_duplicate_job_cancel_even_false_is_denied(self):
        changed = JOB_CANCEL.replace(
            "      cancel-in-progress: true",
            "      cancel-in-progress: false\n      cancel-in-progress: false"
        )
        self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_duplicate_job_concurrency_even_safe_is_denied(self):
        changed = JOB_CANCEL.replace(
            "    concurrency:\n",
            "    concurrency:\n      group: previous\n      cancel-in-progress: false\n"
            "    concurrency:\n"
        )
        self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_standard_space_after_colon_still_allowed(self):
        changed = SELFHOST.replace(
            "cancel-in-progress: false", "cancel-in-progress:  false"
        )
        self.assertEqual([], scanner.risks(changed))

    def test_yaml_explicit_complex_policy_keys_are_denied(self):
        for key in ("concurrency", "cancel-in-progress"):
            with self.subTest(key=key):
                changed = CANCEL.replace(
                    key + ":", "? " + key + "\n: "
                )
                self.assertRaises(scanner.Unverifiable, scanner.risks, changed)

    def test_quoted_concurrency_key_is_denied(self):
        text = CANCEL.replace("concurrency:", "'concurrency':")
        self.assertRaises(scanner.Unverifiable, scanner.risks, text)

    def test_quoted_cancel_key_is_denied(self):
        text = CANCEL.replace("cancel-in-progress:", "'cancel-in-progress':")
        self.assertRaises(scanner.Unverifiable, scanner.risks, text)

    def test_dynamic_runner_denied(self):
        self.assertTrue(scanner.risks(DYNAMIC))

    def test_reusable_job_denied(self):
        self.assertTrue(scanner.risks(CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "uses: ./workflow.yml"
        )))

    def test_job_concurrency_denied(self):
        self.assertTrue(scanner.risks(JOB_CANCEL))

    def test_hosted_job_concurrency_allowed(self):
        hosted = JOB_CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: ubuntu-24.04"
        )
        self.assertEqual([], scanner.risks(hosted))

    def test_duplicate_job_denied(self):
        duplicated = SAFE.replace("jobs:\n", "jobs:\n  check:\n    runs-on: ubuntu-latest\n")
        self.assertRaises(scanner.Unverifiable, scanner.risks, duplicated)

    def test_missing_trigger_denied(self):
        self.assertRaises(scanner.Unverifiable, scanner.risks,
                          SAFE.replace("on:\n", "trigger:\n"))

    def test_yaml_alias_denied(self):
        self.assertRaises(scanner.Unverifiable, scanner.risks,
                          SAFE.replace("runs-on: ubuntu-latest", "runs-on: *runner"))


class GitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ".github/workflows/cancellation.yml"
        self.path.parent.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.root, stderr=subprocess.DEVNULL
        ).decode().strip()

    def commit(self, content):
        self.path.write_text(content, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_new_risk_denied(self):
        base = self.commit(SAFE)
        head = self.commit(CANCEL)
        self.assertEqual([".github/workflows/cancellation.yml"],
                         scanner.evaluate(self.root, base, head))

    def test_modified_legacy_risk_denied(self):
        base = self.commit(CANCEL)
        head = self.commit(CANCEL + "# unrelated edit\n")
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_literal_cross_workflow_vps_collision_denied(self):
        other = self.path.parent / "other-vps.yml"
        other.write_text(SELFHOST, encoding="utf-8")
        base = self.commit(SAFE)
        head = self.commit(SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        ))
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_unknown_other_workflow_cannot_prove_no_collision(self):
        other = self.path.parent / "uncertain.yml"
        other.write_text("name: untrusted\njobs: not-a-map\n", encoding="utf-8")
        base = self.commit(SAFE)
        head = self.commit(
            SAFE.replace("cancel-in-progress: false", "cancel-in-progress: true")
        )
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, base, head)

    def test_dynamic_previous_vps_group_may_match_hosted_group(self):
        # An old expression may evaluate to the new hosted candidate's
        # literal group at runtime; a negative Git-only match is not proof.
        other = self.path.parent / "dynamic-vps.yml"
        other.write_text(
            SELFHOST.replace(
                "group: isolated",
                "group: " + "$" + "{{ github.event.inputs.group }}"
            ),
            encoding="utf-8",
        )
        base = self.commit(SAFE)
        head = self.commit(
            SAFE.replace("cancel-in-progress: false",
                         "cancel-in-progress: true")
        )
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_mixed_static_dynamic_previous_vps_group_denied(self):
        legacy = self.path.parent / "legacy-mixed.yml"
        workflow = SELFHOST.replace("group: isolated", "group: unrelated")
        workflow = workflow.replace(
            "    runs-on: [self-hosted, zcloud, vps]",
            "    runs-on: [self-hosted, zcloud, vps]\n"
            "    concurrency:\n"
            "      group: " + "$" + "{{ inputs.job_group }}\n"
            "      cancel-in-progress: false",
        )
        self.assertEqual({"unrelated"}, scanner.static_concurrency_groups(workflow))
        self.assertTrue(scanner.has_ambiguous_group(workflow))
        legacy.write_text(workflow, encoding="utf-8")
        base = self.commit(SAFE)
        head = self.commit(
            SAFE.replace("cancel-in-progress: false",
                         "cancel-in-progress: true")
        )
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_mixed_static_dynamic_hosted_candidate_denied(self):
        base = self.commit(SAFE)
        candidate = SAFE.replace(
            "    runs-on: ubuntu-latest",
            "    runs-on: ubuntu-latest\n"
            "    concurrency:\n"
            "      group: " + "$" + "{{ github.event.inputs.group }}\n"
            "      cancel-in-progress: false"
        ).replace("  cancel-in-progress: false", "  cancel-in-progress: true")
        self.assertEqual({"isolated"}, scanner.static_concurrency_groups(candidate))
        self.assertTrue(scanner.has_ambiguous_group(candidate))
        head = self.commit(candidate)
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_multiple_literal_groups_are_not_ambiguous(self):
        workflow = SAFE.replace(
            "    runs-on: ubuntu-latest",
            "    runs-on: ubuntu-latest\n"
            "    concurrency:\n"
            "      group: isolated-job\n"
            "      cancel-in-progress: false"
        )
        self.assertEqual({"isolated", "isolated-job"},
                         scanner.static_concurrency_groups(workflow))
        self.assertFalse(scanner.has_ambiguous_group(workflow))

    def test_missing_job_group_is_ambiguous_alongside_static(self):
        workflow = SAFE.replace(
            "    runs-on: ubuntu-latest",
            "    runs-on: ubuntu-latest\n"
            "    concurrency:\n"
            "      cancel-in-progress: false"
        )
        self.assertTrue(scanner.has_ambiguous_group(workflow))

    def test_vps_without_any_group_is_not_collision_evidence(self):
        other = self.path.parent / "isolated-vps.yml"
        other.write_text(
            SELFHOST.replace(
                "concurrency:\n  group: isolated\n  cancel-in-progress: false\n",
                "",
            ),
            encoding="utf-8",
        )
        base = self.commit(SAFE)
        head = self.commit(
            SAFE.replace("cancel-in-progress: false",
                         "cancel-in-progress: true")
        )
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_dynamic_group_owned_only_by_candidate_not_cross_workflow(self):
        self.assertFalse(scanner.literal_group_collision(
            "only.yml", SAFE, {None: {"only.yml"}}
        ))

    def test_different_literal_groups_do_not_collide(self):
        other = self.path.parent / "other-vps.yml"
        other.write_text(SELFHOST.replace("group: isolated", "group: other"),
                         encoding="utf-8")
        base = self.commit(SAFE)
        head = self.commit(SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        ))
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_retired_prior_vps_still_counts_for_collision(self):
        other = self.path.parent / "other-vps.yml"
        other.write_text(SELFHOST, encoding="utf-8")
        base = self.commit(SAFE)
        other.unlink()
        head = self.commit(SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        ))
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_job_level_literal_group_collision_denied(self):
        other = self.path.parent / "legacy-vps.yml"
        other.write_text(
            SELFHOST.replace("group: isolated", "group: shared-live-service"),
            encoding="utf-8",
        )
        base = self.commit(SAFE)
        candidate = JOB_CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: ubuntu-latest"
        )
        head = self.commit(candidate)
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_many_changed_workflows_share_one_collision_census(self):
        from unittest import mock
        second = self.path.parent / "second.yml"
        legacy = self.path.parent / "legacy.yml"
        self.path.write_text(SAFE, encoding="utf-8")
        second.write_text(SAFE, encoding="utf-8")
        legacy.write_text(SELFHOST, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "legacy vps plus hosted consumers")
        base = self.git("rev-parse", "HEAD")
        candidate = SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        )
        self.path.write_text(candidate, encoding="utf-8")
        second.write_text(candidate, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "two concurrent hosted cancellation risks")
        with mock.patch.object(scanner, "build_vps_group_index",
                               wraps=scanner.build_vps_group_index) as census:
            risks = scanner.evaluate(self.root, base, self.git("rev-parse", "HEAD"))
        self.assertEqual(2, len(risks))
        self.assertEqual(1, census.call_count)

    def test_quoted_literal_groups_are_equivalent(self):
        self.assertEqual({"isolated"}, scanner.static_concurrency_groups(SAFE))
        for quoted in ("'isolated'", '"isolated"'):
            self.assertEqual({"isolated"}, scanner.static_concurrency_groups(
                SAFE.replace("group: isolated", "group: " + quoted)
            ))

    def test_literal_group_names_are_github_case_insensitive(self):
        for name in ("PROD", "PrOd", "'PrOd'", '"PROD"'):
            with self.subTest(name=name):
                text = SAFE.replace("group: isolated", "group: " + name)
                self.assertEqual({"prod"}, scanner.static_concurrency_groups(text))

    def test_case_different_groups_collide_with_previous_vps(self):
        legacy = self.path.parent / "different-case.yml"
        legacy.write_text(
            SELFHOST.replace("group: isolated", "group: ProD"),
            encoding="utf-8",
        )
        initial = SAFE.replace("group: isolated", "group: prod")
        base = self.commit(initial)
        head = self.commit(
            initial.replace("cancel-in-progress: false",
                            "cancel-in-progress: true")
        )
        self.assertEqual([".github/workflows/cancellation.yml"],
                         scanner.evaluate(self.root, base, head))

    def test_case_normalization_does_not_confuse_distinct_groups(self):
        self.assertNotEqual(
            scanner.static_concurrency_groups(
                SAFE.replace("group: isolated", "group: prod")
            ),
            scanner.static_concurrency_groups(
                SAFE.replace("group: isolated", "group: production")
            )
        )

    def test_quoted_groups_collide_across_workflows(self):
        previous = self.path.parent / "vps-other.yml"
        previous.write_text(
            SELFHOST.replace("group: isolated", "group: 'isolated'"),
            encoding="utf-8",
        )
        base = self.commit(SAFE)
        head = self.commit(
            SAFE.replace("cancel-in-progress: false", "cancel-in-progress: true")
        )
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_duplicate_group_source_denied(self):
        source = SAFE.replace(
            "  group: isolated",
            "  group: isolated\n  group: shared",
        )
        self.assertRaises(scanner.Unverifiable,
                          scanner.static_concurrency_groups, source)

    def test_inline_quoted_group_is_extracted(self):
        text = SAFE.replace(
            "concurrency:\n  group: isolated\n  cancel-in-progress: false",
            "concurrency: {group: 'shared', cancel-in-progress: true}",
        )
        self.assertEqual({"shared"}, scanner.static_concurrency_groups(text))

    def test_dynamic_cancel_group_denied_without_collision_proof(self):
        base = self.commit(SAFE)
        unknown_group = SAFE.replace(
            "group: isolated", "group: " + "$" + "{{ github.workflow }}"
        ).replace("cancel-in-progress: false", "cancel-in-progress: true")
        head = self.commit(unknown_group)
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_missing_cancel_group_denied_without_collision_proof(self):
        base = self.commit(SAFE)
        changed = SAFE.replace("  group: isolated\n", "").replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        )
        head = self.commit(changed)
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_dynamic_group_is_not_claimed_literal_collision(self):
        self.assertEqual(set(), scanner.static_concurrency_groups(
            SAFE.replace("group: isolated", "group: " + "$" + "{{ github.workflow }}")
        ))

    def test_prior_live_vps_can_be_cancelled_by_new_hosted_global(self):
        # The old workflow's runner can still be executing when the new one
        # claims the same concurrency group, even if new jobs run on GitHub.
        base = self.commit(SELFHOST)
        head = self.commit(SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        ))
        self.assertEqual([".github/workflows/cancellation.yml"],
                         scanner.evaluate(self.root, base, head))
        self.assertEqual([], scanner.risks(
            SAFE.replace("cancel-in-progress: false", "cancel-in-progress: true")
        ))

    def test_prior_live_vps_can_be_cancelled_by_new_hosted_job(self):
        base = self.commit(SELFHOST)
        hosted_with_job_cancel = JOB_CANCEL.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: ubuntu-latest"
        )
        head = self.commit(hosted_with_job_cancel)
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_prior_vps_hosted_transition_without_cancel_allowed(self):
        base = self.commit(SELFHOST)
        head = self.commit(SAFE)
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_prior_hosted_to_hosted_with_cancel_allowed(self):
        base = self.commit(SAFE)
        head = self.commit(SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        ))
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_prior_dynamic_runner_to_hosted_with_cancel_denied(self):
        previous = SELFHOST.replace(
            "runs-on: [self-hosted, zcloud, vps]",
            "runs-on: " + "$" + "{{ matrix.runner }}"
        )
        base = self.commit(previous)
        head = self.commit(SAFE.replace(
            "cancel-in-progress: false", "cancel-in-progress: true"
        ))
        self.assertEqual(1, len(scanner.evaluate(self.root, base, head)))

    def test_safe_reduction_passes(self):
        base = self.commit(CANCEL)
        head = self.commit(SELFHOST)
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_hosted_change_passes(self):
        base = self.commit(SAFE)
        head = self.commit(SAFE.replace("echo safe", "echo tested"))
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_symlink_blob_denied(self):
        base = self.commit(SAFE)
        self.path.unlink()
        self.path.symlink_to("/etc/passwd")
        self.git("add", "-A")
        self.git("commit", "-qm", "symlink")
        self.assertRaises(scanner.Unverifiable, scanner.evaluate, self.root,
                          base, self.git("rev-parse", "HEAD"))

    def test_diverged_base_denied(self):
        base = self.commit(SAFE)
        feature = self.commit(CANCEL)
        self.git("reset", "--hard", base)
        main = self.commit(SAFE + "# diverged main\n")
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, main, feature)

    def test_executable_workflow_blob_denied(self):
        base = self.commit(SAFE)
        self.path.chmod(0o755)
        self.git("add", ".")
        self.git("commit", "-qm", "executable workflow mode")
        head = self.git("rev-parse", "HEAD")
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, base, head)

    def test_invalid_utf8_workflow_blob_denied(self):
        base = self.commit(SAFE)
        self.path.write_bytes(b"name: invalid\xff\n")
        self.git("add", ".")
        self.git("commit", "-qm", "invalid workflow bytes")
        head = self.git("rev-parse", "HEAD")
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, base, head)

    def test_oversized_workflow_blob_denied(self):
        base = self.commit(SAFE)
        self.path.write_text(SAFE + ("# bounded blob proof\n" * 7000),
                             encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "oversized workflow blob")
        head = self.git("rev-parse", "HEAD")
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, base, head)

    def test_safe_rename_is_tracked_without_false_vps_alarm(self):
        base = self.commit(SAFE)
        moved = self.path.with_name("renamed.yml")
        self.path.rename(moved)
        self.git("add", "-A")
        self.git("commit", "-qm", "workflow rename")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual([], scanner.evaluate(self.root, base, head))

    def test_risky_workflow_rename_is_denied(self):
        base = self.commit(SAFE)
        moved = self.path.with_name("renamed.yml")
        self.path.rename(moved)
        moved.write_text(CANCEL, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "risk on workflow rename")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual([".github/workflows/renamed.yml"],
                         scanner.evaluate(self.root, base, head))

    def test_ambiguous_revision_denied(self):
        head = self.commit(SAFE)
        self.assertRaises(scanner.Unverifiable, scanner.evaluate,
                          self.root, "main", head)

    def test_inventory_classifies_safe_risky_unknown(self):
        self.path.write_text(SAFE)
        (self.path.parent / "risk.yml").write_text(CANCEL)
        (self.path.parent / "unparseable.yml").write_text("bad: bytes\n")
        self.git("add", ".")
        self.git("commit", "-qm", "three-class fixture")
        counts = scanner.inventory(self.root, self.git("rev-parse", "HEAD"))
        self.assertEqual({"scanned": 3, "safe": 1, "risky": 1, "unknown": 1,
                          "risky_manual_only": 0,
                          "risky_nonmanual_or_unknown": 1}, counts)

    def test_inventory_detects_manual_only_debt_separately(self):
        manual = CANCEL.replace("pull_request:", "workflow_dispatch:")
        self.path.write_text(manual, encoding="utf-8")
        (self.path.parent / "automatic.yml").write_text(
            CANCEL, encoding="utf-8"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "manual-versus-auto-cohort")
        observed = scanner.inventory(self.root, self.git("rev-parse", "HEAD"))
        self.assertEqual(2, observed["risky"])
        self.assertEqual(1, observed["risky_manual_only"])
        self.assertEqual(1, observed["risky_nonmanual_or_unknown"])

    def test_manual_detection_refuses_ambiguous_event_source(self):
        manual = CANCEL.replace("pull_request:", "workflow_dispatch:")
        self.assertTrue(scanner.is_manual_only(manual))
        extra = manual.replace("  workflow_dispatch:", "  workflow_dispatch:\n  push:")
        self.assertFalse(scanner.is_manual_only(extra))
        self.assertFalse(scanner.is_manual_only(
            manual.replace("  workflow_dispatch:", "  schedule:")
        ))

    def test_triage_prioritizes_nonmanual_then_manual_and_unknown(self):
        self.path.write_text(CANCEL)
        (self.path.parent / "manual.yml").write_text(
            CANCEL.replace("pull_request:", "workflow_dispatch:")
        )
        (self.path.parent / "bad.yml").write_text("not: a workflow\n")
        (self.path.parent / "clean.yml").write_text(SAFE)
        self.git("add", ".")
        self.git("commit", "-qm", "triage cohorts")
        entries, count = scanner.triage(self.root, self.git("rev-parse", "HEAD"))
        self.assertEqual(3, count)
        self.assertEqual(["automatic_or_unknown", "manual_only",
                          "unknown_source"], [x[0] for x in entries])
        self.assertEqual([".github/workflows/cancellation.yml",
                          ".github/workflows/manual.yml",
                          ".github/workflows/bad.yml"], [x[1] for x in entries])

    def test_triage_is_bounded_and_deterministic(self):
        self.path.write_text(CANCEL)
        (self.path.parent / "second.yml").write_text(CANCEL)
        self.git("add", ".")
        self.git("commit", "-qm", "triage limit")
        sha = self.git("rev-parse", "HEAD")
        first, total = scanner.triage(self.root, sha, 1)
        second, observed = scanner.triage(self.root, sha, 1)
        self.assertEqual(2, total)
        self.assertEqual(total, observed)
        self.assertEqual(first, second)
        self.assertEqual(1, len(first))
        self.assertEqual("automatic_or_unknown", first[0][0])

    def test_triage_prefix_only_selects_requested_lane(self):
        self.path.write_text(CANCEL)
        (self.path.parent / "zcloud-one.yml").write_text(CANCEL)
        (self.path.parent / "zcloud-two.yml").write_text(CANCEL)
        (self.path.parent / "ftmo-one.yml").write_text(CANCEL)
        self.git("add", ".")
        self.git("commit", "-qm", "cross-lane risk shortlist")
        sha = self.git("rev-parse", "HEAD")
        entries, count = scanner.triage(self.root, sha, 12, "zcloud-")
        self.assertEqual(2, count)
        self.assertTrue(all(path.startswith(".github/workflows/zcloud-")
                            for _, path in entries))

    def test_triage_prefix_rejects_untrusted_paths(self):
        sha = self.commit(CANCEL)
        for prefix in ("../", "zcloud/other", "", "a" * 41, " "):
            with self.subTest(prefix=prefix):
                self.assertRaises(scanner.Unverifiable, scanner.triage,
                                  self.root, sha, 12, prefix)

    def test_triage_cli_lane_is_path_only(self):
        self.path.write_text(CANCEL)
        (self.path.parent / "zcloud-safe.yml").write_text(CANCEL)
        self.git("add", ".")
        self.git("commit", "-qm", "scoped risk")
        sha = self.git("rev-parse", "HEAD")
        from io import StringIO
        from contextlib import redirect_stdout
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = scanner.main(["--triage-head", sha,
                                 "--triage-prefix", "zcloud-",
                                 "--repo-root", str(self.root)])
        self.assertEqual(0, code)
        self.assertIn("total=1 shown=1", stdout.getvalue())
        self.assertNotIn("cancellation.yml", stdout.getvalue())
        self.assertIn("zcloud-safe.yml", stdout.getvalue())

    def test_triage_refuses_untrusted_ref_and_bad_limits(self):
        self.commit(SAFE)
        sha = self.git("rev-parse", "HEAD")
        for revision, limit in (("HEAD", 12), (sha, 0),
                                (sha, 26), (sha, -2)):
            with self.subTest(revision=revision, limit=limit):
                self.assertRaises(scanner.Unverifiable, scanner.triage,
                                  self.root, revision, limit)

    def test_triage_output_is_source_free_and_non_authorizing(self):
        self.commit(CANCEL)
        sha = self.git("rev-parse", "HEAD")
        from io import StringIO
        from contextlib import redirect_stdout
        out = StringIO()
        with redirect_stdout(out):
            status = scanner.main(["--triage-head", sha, "--triage-limit", "1",
                                   "--repo-root", str(self.root)])
        self.assertEqual(0, status)
        self.assertIn("TRIAGE=OBSERVED", out.getvalue())
        self.assertIn("sha=" + sha, out.getvalue())
        self.assertIn("VPS_MUTATION_AUTHORIZED=false", out.getvalue())
        self.assertIn(".github/workflows/cancellation.yml", out.getvalue())
        for denied in ("echo safe", "runs-on:", "token:"):
            self.assertNotIn(denied, out.getvalue())

    def test_triage_mode_is_exclusive(self):
        sha = self.commit(SAFE)
        from io import StringIO
        from contextlib import redirect_stdout
        output = StringIO()
        with redirect_stdout(output):
            code = scanner.main(["--triage-head", sha, "--inventory-head", sha,
                                 "--repo-root", str(self.root)])
        self.assertEqual(2, code)
        self.assertIn("TRIAGE=DENY", output.getvalue())

    def test_inventory_does_not_accept_non_workflow_paths(self):
        self.commit(SAFE)
        (self.path.parent / "NOT-A-WORKFLOW.txt").write_text("unexpected")
        self.git("add", ".")
        self.git("commit", "-qm", "unexpected workflow path")
        self.assertRaises(scanner.Unverifiable, scanner.inventory,
                          self.root, self.git("rev-parse", "HEAD"))

    def test_inventory_cli_prints_counts_without_workflow_contents(self):
        self.commit(CANCEL)
        sha = self.git("rev-parse", "HEAD")
        from io import StringIO
        from contextlib import redirect_stdout
        report = StringIO()
        with redirect_stdout(report):
            code = scanner.main(["--repo-root", str(self.root),
                                 "--inventory-head", sha])
        self.assertEqual(0, code)
        self.assertIn("risky=1", report.getvalue())
        self.assertIn("sha=" + sha, report.getvalue())
        self.assertIn("VPS_MUTATION_AUTHORIZED=false", report.getvalue())
        self.assertNotIn("echo safe", report.getvalue())
        self.assertNotIn("runs-on:", report.getvalue())

    def test_inventory_rejects_modes_mixed(self):
        sha = self.commit(SAFE)
        from io import StringIO
        from contextlib import redirect_stdout
        report = StringIO()
        with redirect_stdout(report):
            rc = scanner.main(["--repo-root", str(self.root),
                               "--inventory-head", sha, "--head", sha])
        self.assertEqual(2, rc)
        self.assertIn("INVENTORY=DENY", report.getvalue())

    def test_inventory_rejects_untrusted_sha(self):
        self.commit(SAFE)
        self.assertRaises(scanner.Unverifiable, scanner.inventory,
                          self.root, "HEAD")

    def test_output_denies_authority_without_raw_steps(self):
        from io import StringIO
        from contextlib import redirect_stdout
        base = self.commit(SAFE)
        head = self.commit(CANCEL)
        output = StringIO()
        with redirect_stdout(output):
            status = scanner.main(["--base", base, "--head", head,
                                   "--repo-root", str(self.root)])
        self.assertEqual(1, status)
        self.assertIn("VPS_MUTATION_AUTHORIZED=false", output.getvalue())
        self.assertNotIn("echo safe", output.getvalue())


class WorkflowSourceTest(unittest.TestCase):
    """Protect the exact hosted ratchet trigger and its non-mutating authority."""

    @classmethod
    def setUpClass(cls):
        cls.source = (MODULE.parents[1] /
                      ".github/workflows/zcloud-deployops-cancellation-ratchet-20261014.yml"
                     ).read_text(encoding="utf-8")

    def test_triggered_by_any_changed_main_workflow(self):
        self.assertIn('      - ".github/workflows/**"', self.source)
        self.assertIn("      - main", self.source)
        self.assertIn("  push:\n", self.source)
        self.assertIn("  pull_request:\n", self.source)

    def test_source_path_and_tests_retrigger_on_change(self):
        for path in ("scripts/zcloud_deployops_cancellation_ratchet_20261014.py",
                     "tests/test_zcloud_deployops_cancellation_ratchet_20261014.py"):
            self.assertGreaterEqual(self.source.count(path), 2)

    def test_no_selfhosted_or_write_permissions(self):
        self.assertIn("    runs-on: ubuntu-latest", self.source)
        self.assertNotIn("runs-on: [self-hosted", self.source)
        self.assertNotIn("    runs-on: self-hosted", self.source)
        self.assertIn("permissions:\n  contents: read", self.source)
        for denied in ("contents: write", "actions: write",
                       "id-token: write", "pull-requests: write"):
            self.assertNotIn(denied, self.source)

    def test_checkout_exact_commit_and_credentials_not_persisted(self):
        self.assertIn("github.event.pull_request.head.sha || github.sha",
                      self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXACT_HEAD"', self.source)
        self.assertIn("fetch-depth: 0", self.source)

    def test_concurrency_can_never_cancel_running_job(self):
        self.assertIn("  cancel-in-progress: false", self.source)
        self.assertEqual(1, self.source.count("cancel-in-progress: false"))

    def test_both_snapshot_census_and_candidate_gate(self):
        self.assertIn('--base "$EXACT_BASE" --head "$EXACT_HEAD"', self.source)
        self.assertIn('--inventory-head "$EXACT_BASE"', self.source)
        self.assertIn('--inventory-head "$EXACT_HEAD"', self.source)

    def test_no_host_mutation_commands(self):
        for forbidden in ("systemctl", "sudo ", "gh run cancel", "docker ",
                          "scp ", "rsync ", "gh workflow run ", "kill -9"):
            self.assertNotIn(forbidden, self.source)

    def test_bounded_hosted_timeout(self):
        self.assertIn("    timeout-minutes: 5", self.source)


if __name__ == "__main__":
    unittest.main()
