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
