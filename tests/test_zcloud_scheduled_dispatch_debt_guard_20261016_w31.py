"""Real-Git adversarial regressions for scheduled redispatch debt ratchet."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/zcloud_scheduled_dispatch_debt_guard_20261016_w31.py"
spec = importlib.util.spec_from_file_location("scheduled_dispatch_guard", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

LEGACY = """name: already-existing
on:
  schedule:
    - cron: '*/15 * * * *'
jobs:
  rec:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run zcloud-regression-smoke.yml
"""
PLAIN = """name: hosted-no-dispatch
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


def cmd(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


class WorkflowAdmission(unittest.TestCase):
    def test_guard_runs_for_every_main_targeting_pr(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        self.assertIn("  pull_request:\n    branches: [main]", source)
        trigger = source.split("  pull_request:", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("    paths:", trigger)
        self.assertNotIn("    paths-ignore:", trigger)
        self.assertNotIn("    branches-ignore:", trigger)
        self.assertIn("synchronize", trigger)
        self.assertIn("reopened", trigger)
        self.assertIn("ready_for_review", trigger)
        self.assertIn("edited", trigger)

    def test_all_main_pushes_audited_with_before_and_after_identity(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        self.assertIn("  push:\n    branches: [main]", source)
        self.assertIn("github.event_name == 'push'", source)
        self.assertIn("EXPECTED_BASE: ${{ github.event.pull_request.base.sha || github.event.before }}", source)
        self.assertIn("EXPECTED_MAIN: ${{ github.event_name == 'push' && github.sha || github.event.pull_request.base.sha }}", source)
        self.assertIn('expected_main="$EXPECTED_MAIN"', source)
        self.assertIn("if: github.event_name == 'pull_request'", source)

    def test_fork_source_fails_not_skips_required_check(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        admission = source.split("  source-only:", 1)[1].split("    runs-on:", 1)[0]
        self.assertNotIn("head.repo.full_name ==", admission)
        self.assertIn("Reject fork or unknown source before checkout", source)
        self.assertIn('test "$HEAD_REPO" = "$BASE_REPO"', source)
        self.assertIn("HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}", source)
        self.assertLess(
            source.index("Reject fork or unknown source before checkout"),
            source.index("Checkout exact candidate without persisted credentials"),
        )

    def test_source_gate_requires_fresh_main_before_and_after_scan(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        self.assertEqual(
            source.count("git ls-remote --exit-code https://github.com/Zennay/zCloud.git refs/heads/main"),
            2,
        )
        self.assertEqual(source.count('test "$current_main" = "$expected_main"'), 2)
        self.assertIn("expected_main=", source)
        self.assertIn("refs/heads/main", source)
        self.assertIn('test "$(git cat-file -t "$EXPECTED_BASE")" = commit', source)

    def test_hosted_only_immutable_pr_checkout(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", source)
        self.assertNotIn("runs-on: [self-hosted", source)
        self.assertNotIn("actions: write", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"', source)


class RealGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        cmd(self.repo, "init", "-q")
        cmd(self.repo, "config", "user.name", "Source Guard")
        cmd(self.repo, "config", "user.email", "guard@example.invalid")
        (self.repo / ".github/workflows").mkdir(parents=True)
        self.put("existing.yml", LEGACY)
        self.put("plain.yml", PLAIN)
        self.base = self.commit()

    def put(self, name, value):
        (self.repo / ".github/workflows" / name).write_text(value, encoding="utf-8")

    def commit(self):
        cmd(self.repo, "add", ".github")
        cmd(self.repo, "commit", "-qm", "test fixture", "--allow-empty")
        return cmd(self.repo, "rev-parse", "HEAD")

    def check(self, candidate, expected):
        result = guard.evaluate(self.repo, self.base, candidate)
        self.assertEqual(result["status"], expected)
        self.assertEqual(result["deploy_authorized"], False)
        self.assertEqual(result["mutation_performed"], False)
        return result

    def test_preexisting_risk_grandfathered_unchanged(self):
        result = self.check(self.base, "pass")
        self.assertEqual(result["baseline_sites"], 1)
        self.assertEqual(result["new_sites"], 0)

    def test_benign_edit_to_existing_scheduled_workflow(self):
        self.put("existing.yml", LEGACY + "\n# documented debt\n")
        self.check(self.commit(), "pass")

    def test_new_schedule_with_gh_dispatch_rejected(self):
        self.put("fresh.yml", LEGACY)
        result = self.check(self.commit(), "reject")
        self.assertEqual(result["new_sites"], 1)
        self.assertEqual(result["changed_workflows_with_new_sites"], [".github/workflows/fresh.yml"])

    def test_second_dispatch_in_existing_workflow_rejected(self):
        self.put("existing.yml", LEGACY + "      - run: gh workflow run zcloud-vps-deploy.yml\n")
        self.assertEqual(self.check(self.commit(), "reject")["new_sites"], 1)

    def test_new_schedule_on_existing_manual_dispatch_rejected(self):
        self.put("plain.yml", PLAIN.replace("on: pull_request", "on: [pull_request, schedule]")
                 .replace("echo ok", "gh workflow run foo.yml"))
        self.check(self.commit(), "reject")

    def test_rest_dispatch_rejected(self):
        self.put("rest.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "curl -X POST https://api.github.com/repos/Zennay/zCloud/actions/workflows/foo.yml/dispatches"
        ))
        self.check(self.commit(), "reject")

    def test_js_api_dispatch_rejected(self):
        self.put("api.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "octokit.rest.actions.createWorkflowDispatch({})"
        ))
        self.check(self.commit(), "reject")

    def test_dynamic_workflow_identifier_cannot_hide_api_dispatch(self):
        self.put("dynamic.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "gh api -X POST repos/$GITHUB_REPOSITORY/actions/workflows/$TARGET/dispatches"
        ))
        self.check(self.commit(), "reject")

    def test_repo_dispatch_rest_endpoint_rejected(self):
        self.put("event.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "gh api -X POST repos/Zennay/zCloud/dispatches"
        ))
        self.check(self.commit(), "reject")

    def test_repo_dispatch_octokit_endpoint_rejected(self):
        self.put("octokit.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "github.rest.repos.createDispatchEvent({event_type: 'deploy'})"
        ))
        self.check(self.commit(), "reject")

    def test_repo_dispatch_generic_sdk_call_rejected(self):
        self.put("sdk.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "client.createRepositoryDispatch({})"
        ))
        self.check(self.commit(), "reject")

    def test_non_scheduled_repo_dispatch_remains_unrestricted(self):
        self.put("manual.yml", PLAIN.replace(
            "echo ok", "gh api -X POST repos/Zennay/zCloud/dispatches"
        ))
        self.check(self.commit(), "pass")

    def test_same_dispatch_in_non_scheduled_workflow_is_allowed(self):
        self.put("manual.yml", PLAIN.replace("echo ok", "gh workflow run foo.yml"))
        self.check(self.commit(), "pass")

    def test_comments_not_new_dispatches(self):
        self.put("existing.yml", LEGACY + "      # gh workflow run foo.yml\n")
        self.check(self.commit(), "pass")

    def test_schedule_string_in_script_is_not_trigger(self):
        self.put("script.yml", PLAIN.replace("echo ok", "echo schedule: && gh workflow run foo.yml"))
        self.check(self.commit(), "pass")

    def test_quoted_yaml_on_detected(self):
        self.put("quoted.yml", LEGACY.replace("on:", "'on':"))
        self.check(self.commit(), "reject")

    def test_alias_trigger_rejected_if_dispatch_present(self):
        self.put("alias.yml", LEGACY.replace("on:\n  schedule:\n    - cron: '*/15 * * * *'", "on: *trigger"))
        self.check(self.commit(), "reject")


    def test_existing_recovery_split_url_counts_as_legacy(self):
        # Mirrors production-status-recovery where two Python string literals
        # compose the REST dispatch endpoint.
        real_shape = """name: real-recovery
on:
  schedule:
    - cron: '*/15 * * * *'
jobs:
  rearm:
    runs-on: ubuntu-latest
    steps:
      - run: |
          request = Request(
              f"https://api.github.com/repos/{repo}/actions/workflows/"
              "zcloud-regression-smoke.yml/dispatches",
              method="POST")
"""
        self.put("existing.yml", real_shape)
        clean_base = self.commit()
        self.assertEqual(guard.evaluate(self.repo, clean_base, clean_base)["baseline_sites"], 1)
        self.put("plain.yml", PLAIN.replace("on: pull_request", "on: schedule")
                 .replace("echo ok", "gh workflow run extra.yml"))
        result = guard.evaluate(self.repo, clean_base, self.commit())
        self.assertEqual(result["new_sites"], 1)

    def test_inline_scalar_schedule_is_detected(self):
        self.put("new.yml", LEGACY.replace(
            "on:\n  schedule:\n    - cron: '*/15 * * * *'", "on: schedule"))
        self.check(self.commit(), "reject")

    def test_compact_single_schedule_sequence_is_rejected(self):
        for value in ("[schedule]", "['schedule']", '["schedule"]'):
            with self.subTest(value=value):
                self.assertTrue(guard.scheduled(LEGACY.replace(
                    "on:\n  schedule:\n    - cron: '*/15 * * * *'",
                    "on: " + value,
                )))
        self.put("compact.yml", LEGACY.replace(
            "on:\n  schedule:\n    - cron: '*/15 * * * *'",
            "on: [schedule]",
        ))
        self.check(self.commit(), "reject")

    def test_compact_list_first_schedule_with_other_event(self):
        source = LEGACY.replace(
            "on:\n  schedule:\n    - cron: '*/15 * * * *'",
            "on: [schedule,push]",
        )
        self.assertTrue(guard.scheduled(source))
        self.put("compact.yml", source)
        self.check(self.commit(), "reject")

    def test_repository_option_between_gh_workflow_and_run(self):
        for command in (
            "gh workflow -R Zennay/zCloud run deploy.yml",
            "gh workflow --repo Zennay/zCloud run deploy.yml",
            "gh workflow --repo=Zennay/zCloud run deploy.yml",
            "gh -R Zennay/zCloud workflow run deploy.yml",
            "gh --repo=Zennay/zCloud workflow run deploy.yml",
        ):
            with self.subTest(command=command):
                self.assertEqual(guard.dispatch_count(LEGACY.replace(
                    "gh workflow run zcloud-regression-smoke.yml", command
                )), 1)
        self.put("repo-option.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "gh -R Zennay/zCloud workflow run deploy.yml",
        ))
        self.check(self.commit(), "reject")

    def test_shell_continuation_cannot_hide_dispatch(self):
        self.put("new.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "gh workflow \\\n              run zcloud-regression-smoke.yml"))
        self.check(self.commit(), "reject")

    def test_deletion_of_legacy_site_is_allowed(self):
        self.put("existing.yml", LEGACY.replace("gh workflow run zcloud-regression-smoke.yml", "echo safe"))
        self.check(self.commit(), "pass")

    def test_dispatch_target_substitution_denied_without_count_growth(self):
        self.put("existing.yml", LEGACY.replace(
            "zcloud-regression-smoke.yml", "zcloud-vps-deploy.yml"
        ))
        result = self.check(self.commit(), "reject")
        self.assertEqual(result["baseline_sites"], result["candidate_sites"])
        self.assertEqual(result["new_sites"], 1)

    def test_dispatch_method_substitution_denied_without_count_growth(self):
        self.put("existing.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "github.rest.actions.createWorkflowDispatch({workflow_id: 1})"
        ))
        result = self.check(self.commit(), "reject")
        self.assertEqual(result["new_sites"], 1)

    def test_dispatch_duplicate_denied_even_if_same_line(self):
        self.put("existing.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "gh workflow run zcloud-regression-smoke.yml && gh workflow run zcloud-regression-smoke.yml"
        ))
        result = self.check(self.commit(), "reject")
        self.assertEqual(result["new_sites"], 2)

    def test_reorder_same_dispatch_line_keeps_site_identity(self):
        self.put("existing.yml", LEGACY.replace(
            "      - run: gh workflow run zcloud-regression-smoke.yml",
            "      - name: Recovery\n        run: gh workflow run zcloud-regression-smoke.yml"
        ))
        # Whitespace and format changes count as conservative new debt.
        self.check(self.commit(), "reject")

    def test_unrelated_job_step_does_not_change_fingerprint(self):
        self.put("existing.yml", LEGACY.replace(
            "      - run: gh workflow run zcloud-regression-smoke.yml",
            "      - run: echo other\n      - run: gh workflow run zcloud-regression-smoke.yml"
        ))
        self.check(self.commit(), "pass")

    def test_new_scheduled_reusable_workflow_dispatch_rejected(self):
        caller = """name: schedule-delegation
on:
  schedule:
    - cron: '0 8 * * *'
jobs:
  delegate:
    uses: ./.github/workflows/runner.yml
"""
        helper = """name: runner
on: workflow_call
jobs:
  execute:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run production.yml
"""
        self.put("schedule-caller.yml", caller)
        self.put("runner.yml", helper)
        result = self.check(self.commit(), "reject")
        self.assertGreaterEqual(result["new_sites"], 1)

    def test_existing_scheduled_caller_detects_new_helper_dispatch(self):
        caller = """on:
  schedule:
    - cron: '0 8 * * *'
jobs:
  delegate:
    uses: ./.github/workflows/runner.yml
"""
        helper = """on: workflow_call
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo safe
"""
        self.put("schedule-caller.yml", caller)
        self.put("runner.yml", helper)
        base = self.commit()
        self.put("runner.yml", helper.replace("echo safe", "gh workflow run release.yml"))
        candidate = self.commit()
        result = guard.evaluate(self.repo, base, candidate)
        self.assertEqual(result["status"], "reject")
        self.assertIn(".github/workflows/schedule-caller.yml", result["changed_workflows_with_new_sites"])

    def test_existing_scheduled_delegate_is_stable_when_unchanged(self):
        self.put("schedule-caller.yml", """on:
  schedule:
    - cron: '0 8 * * *'
jobs:
  delegate:
    uses: ./.github/workflows/runner.yml
""")
        self.put("runner.yml", """on: workflow_call
jobs:
  delegate:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run deploy.yml
""")
        base = self.commit()
        report = guard.evaluate(self.repo, base, base)
        self.assertEqual(report["new_sites"], 0)
        self.assertEqual(report["status"], "pass")

    def test_remote_reusable_workflow_delegation_denied(self):
        self.put("remote.yml", """on: schedule
jobs:
  call:
    uses: outside/repo/.github/workflows/deploy.yml@main
""")
        self.check(self.commit(), "reject")

    def test_cyclic_local_reusable_workflows_bounded(self):
        self.put("schedule-caller.yml", """on: schedule
jobs:
  next:
    uses: ./.github/workflows/loop.yml
""")
        self.put("loop.yml", """on: workflow_call
jobs:
  next:
    uses: ./.github/workflows/schedule-caller.yml
""")
        report = self.check(self.commit(), "reject")
        self.assertLessEqual(report["new_sites"], 4)

    def put_action(self, directory, value):
        action = self.repo / ".github" / "actions" / directory / "action.yml"
        action.parent.mkdir(parents=True, exist_ok=True)
        action.write_text(value, encoding="utf-8")

    def test_scheduled_composite_action_indirect_dispatch_rejected(self):
        self.put("schedule-actions.yml", """on: schedule
jobs:
  rearm:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/rearm
""")
        self.put_action("rearm", """name: rearm
runs:
  using: composite
  steps:
    - shell: bash
      run: gh workflow run deploy.yml
""")
        result = self.check(self.commit(), "reject")
        self.assertGreaterEqual(result["new_sites"], 2)

    def test_existing_scheduled_action_detects_helper_mutation(self):
        self.put("schedule-actions.yml", """on: schedule
jobs:
  rearm:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/rearm
""")
        self.put_action("rearm", """name: rearm
runs:
  using: composite
  steps:
    - shell: bash
      run: echo safe
""")
        base = self.commit()
        self.put_action("rearm", """name: rearm
runs:
  using: composite
  steps:
    - shell: bash
      run: gh workflow run deploy.yml
""")
        verdict = guard.evaluate(self.repo, base, self.commit())
        self.assertEqual(verdict["status"], "reject")
        self.assertIn(".github/workflows/schedule-actions.yml", verdict["changed_workflows_with_new_sites"])

    def test_scheduled_action_missing_manifest_is_denied(self):
        self.put("schedule-actions.yml", """on: schedule
jobs:
  rearm:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/nonexistent
""")
        self.check(self.commit(), "reject")

    def test_action_symlink_manifest_denied(self):
        directory = self.repo / ".github" / "actions" / "rearm"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "action.yml").symlink_to("nonexistent")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            guard.evaluate(self.repo, self.base, self.commit())

    def test_git_stdout_bounded_during_read(self):
        # With a tiny cap even ls-tree inventory output is excessive.
        with mock.patch.object(guard, "MAX_TOTAL", 20):
            with self.assertRaisesRegex(ValueError, "streaming bounds"):
                guard.evaluate(self.repo, self.base, self.base)

    def test_git_stream_keeps_exact_sha_bytes(self):
        self.assertEqual(guard.git(self.repo, "rev-parse", "HEAD").decode().strip(), self.base)

    def test_git_nonzero_exit_denies_without_source_echo(self):
        with self.assertRaisesRegex(ValueError, "failed closed"):
            guard.git(self.repo, "show", "nonexistent-tag")

    def test_bad_sha_fails_closed(self):
        with self.assertRaises(ValueError):
            guard.evaluate(self.repo, "main", self.base)

    def test_symlink_workflow_fails_closed(self):
        p = self.repo / ".github/workflows/symlink.yml"
        p.symlink_to("plain.yml")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            guard.evaluate(self.repo, self.base, self.commit())

    def test_opaque_non_utf8_fails_closed(self):
        (self.repo / ".github/workflows/nonutf8.yml").write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "non-UTF8"):
            guard.evaluate(self.repo, self.base, self.commit())


if __name__ == "__main__":
    unittest.main()
