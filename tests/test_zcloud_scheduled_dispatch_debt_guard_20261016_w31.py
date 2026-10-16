"""Real-Git adversarial regressions for scheduled redispatch debt ratchet."""
import importlib.util
import sys
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


class TrustedBaselineAdmission(unittest.TestCase):
    def test_workflow_invokes_base_policy_not_candidate_if_present(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        self.assertIn('base_entry="$(git ls-tree "$EXPECTED_BASE" -- "$source_path")"', source)
        self.assertIn('git show "$EXPECTED_BASE:$source_path" > "$trusted_guard"', source)
        self.assertIn('python3 "$trusted_guard" --base "$EXPECTED_BASE" --candidate "$EXPECTED_HEAD"', source)
        self.assertIn('python3 "$source_path" --base "$EXPECTED_BASE" --candidate "$EXPECTED_HEAD"', source)
        self.assertIn('if [[ -n "$base_entry" ]]; then', source)
        self.assertIn('::warning::BOOTSTRAP ONLY:', source)
        self.assertLess(
            source.index('git show "$EXPECTED_BASE:$source_path" > "$trusted_guard"'),
            source.index('python3 "$trusted_guard" --base "$EXPECTED_BASE" --candidate "$EXPECTED_HEAD"'),
        )

    def test_main_policy_survives_candidate_checker_tampering(self):
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            cmd(repo, "init", "-q")
            cmd(repo, "config", "user.email", "guard@example.invalid")
            cmd(repo, "config", "user.name", "Guard")
            path = repo / "scripts" / "checker.py"
            path.parent.mkdir()
            path.write_text("raise SystemExit(7)\n", encoding="utf-8")
            cmd(repo, "add", "scripts/checker.py")
            cmd(repo, "commit", "-qm", "trusted policy")
            base = cmd(repo, "rev-parse", "HEAD")
            path.write_text("print('always pass')\n", encoding="utf-8")
            cmd(repo, "add", "scripts/checker.py")
            cmd(repo, "commit", "-qm", "candidate tampering")
            candidate = cmd(repo, "rev-parse", "HEAD")
            trusted = subprocess.check_output([
                "git", "-C", str(repo), "show", base + ":scripts/checker.py",
            ])
            self.assertNotEqual(
                trusted, subprocess.check_output([
                    "git", "-C", str(repo), "show", candidate + ":scripts/checker.py",
                ])
            )
            trusted_path = repo / "trusted.py"
            trusted_path.write_bytes(trusted)
            self.assertEqual(subprocess.run(
                [sys.executable, str(trusted_path)], check=False,
            ).returncode, 7)
            self.assertEqual(subprocess.run(
                [sys.executable, str(path)], check=False, stdout=subprocess.DEVNULL,
            ).returncode, 0)


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

    def test_control_character_manifest_filename_is_rejected(self):
        suspicious = self.repo / ".github/workflows/launch\nspoof.yml"
        suspicious.write_text(LEGACY, encoding="utf-8")
        candidate = self.commit()
        with self.assertRaisesRegex(ValueError, "unsafe manifest"):
            guard.evaluate(self.repo, self.base, candidate)

    def test_action_control_character_filename_is_rejected(self):
        suspicious = self.repo / ".github/actions/invisible\rhidden"
        suspicious.mkdir(parents=True, exist_ok=True)
        (suspicious / "action.yml").write_text("runs: {using: composite, steps: []}", encoding="utf-8")
        candidate = self.commit()
        with self.assertRaisesRegex(ValueError, "unsafe manifest"):
            guard.evaluate(self.repo, self.base, candidate)

    def test_action_symlink_manifest_denied(self):
        directory = self.repo / ".github" / "actions" / "rearm"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "action.yml").symlink_to("nonexistent")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            guard.evaluate(self.repo, self.base, self.commit())

    def test_nested_composite_action_dispatch_is_reachable(self):
        self.put("schedule-actions.yml", """on: schedule
jobs:
  act:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/outer
""")
        self.put_action("outer", """name: outer
runs:
  using: composite
  steps:
    - uses: ./.github/actions/inner
""")
        self.put_action("inner", """name: inner
runs:
  using: composite
  steps:
    - shell: bash
      run: gh workflow run release.yml
""")
        result = self.check(self.commit(), "reject")
        self.assertGreaterEqual(result["new_sites"], 3)

    def test_action_yaml_alternative_is_traversed(self):
        self.put("schedule-actions.yml", """on: schedule
jobs:
  act:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/alternate
""")
        target = self.repo / ".github/actions/alternate/action.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("""name: alternate
runs:
  using: composite
  steps:
    - shell: bash
      run: gh workflow run release.yml
""", encoding="utf-8")
        result = self.check(self.commit(), "reject")
        self.assertGreaterEqual(result["new_sites"], 2)

    def test_unchanged_action_graph_keeps_existing_debt(self):
        self.put("schedule-actions.yml", """on: schedule
jobs:
  act:
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
        base = self.commit()
        self.assertEqual(guard.evaluate(self.repo, base, base)["new_sites"], 0)
        self.put_action("rearm", """name: rearm
# Documentation-only change must not add a dispatch site.
runs:
  using: composite
  steps:
    - shell: bash
      run: gh workflow run deploy.yml
""")
        self.assertEqual(guard.evaluate(self.repo, base, self.commit())["status"], "pass")

    def test_node_action_entrypoint_dispatch_rejected(self):
        self.put("scheduled-node.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/notify
""")
        self.put_action("notify", """name: notify
runs:
  using: node20
  main: dist/index.js
""")
        js = self.repo / ".github/actions/notify/dist/index.js"
        js.parent.mkdir(parents=True)
        js.write_text(
            "await github.rest.actions.createWorkflowDispatch({workflow_id: 'deploy.yml'});\n",
            encoding="utf-8",
        )
        result = self.check(self.commit(), "reject")
        self.assertGreaterEqual(result["new_sites"], 2)

    def test_changed_node_action_entrypoint_is_tracked(self):
        self.put("scheduled-node.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/notify
""")
        self.put_action("notify", """name: notify
runs:
  using: node20
  main: dist/index.js
""")
        js = self.repo / ".github/actions/notify/dist/index.js"
        js.parent.mkdir(parents=True)
        js.write_text("console.log('safe');\n", encoding="utf-8")
        base = self.commit()
        js.write_text("github.rest.repos.createDispatchEvent({});\n", encoding="utf-8")
        result = guard.evaluate(self.repo, base, self.commit())
        self.assertEqual(result["status"], "reject")
        self.assertIn(".github/workflows/scheduled-node.yml", result["changed_workflows_with_new_sites"])

    def test_missing_node_entrypoint_fails_new_admission(self):
        self.put("scheduled-node.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/missing
""")
        self.put_action("missing", """runs:
  using: node20
  main: index.js
""")
        self.check(self.commit(), "reject")

    def test_script_reference_can_not_escape_action_directory(self):
        self.put("scheduled-node.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/escape
""")
        self.put_action("escape", """runs:
  using: node20
  main: ../../workflows/other.js
""")
        with self.assertRaisesRegex(ValueError, "unsafe action entrypoint"):
            guard.evaluate(self.repo, self.base, self.commit())

    def test_unchanged_node_action_dispatch_does_not_grow_debt(self):
        self.put("scheduled-node.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/notify
""")
        self.put_action("notify", """runs:
  using: node20
  main: dist/index.js
""")
        js = self.repo / ".github/actions/notify/dist/index.js"
        js.parent.mkdir(parents=True)
        js.write_text("github.rest.actions.createWorkflowDispatch({});\n", encoding="utf-8")
        base = self.commit()
        self.assertEqual(guard.evaluate(self.repo, base, base)["status"], "pass")
        self.assertEqual(guard.evaluate(self.repo, base, base)["new_sites"], 0)

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


    def test_quoted_reusable_uses_key_is_traversed(self):
        self.put("schedule-delegate.yml", """on: schedule
jobs:
  delegate:
    'uses' : ./.github/workflows/inner.yml
""")
        self.put("inner.yml", """on: workflow_call
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run dangerous.yml
""")
        result = self.check(self.commit(), "reject")
        self.assertIn(".github/workflows/schedule-delegate.yml",
                      result["changed_workflows_with_new_sites"])

    def test_quoted_composite_uses_key_is_traversed(self):
        self.put("schedule-action.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - "uses" : ./.github/actions/notify
""")
        self.put_action("notify", """runs:
  using: composite
  steps:
    - shell: bash
      run: gh workflow run release.yml
""")
        self.check(self.commit(), "reject")

    def test_quoted_node_action_main_key_is_traversed(self):
        self.put("schedule-node.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/quoted
""")
        self.put_action("quoted", """runs:
  using: node20
  "main" : dist/index.js
""")
        entry = self.repo / ".github/actions/quoted/dist/index.js"
        entry.parent.mkdir(parents=True)
        entry.write_text("console.log('safe');\\n", encoding="utf-8")
        base = self.commit()
        entry.write_text("github.rest.actions.createWorkflowDispatch({});\\n",
                         encoding="utf-8")
        result = guard.evaluate(self.repo, base, self.commit())
        self.assertEqual(result["status"], "reject")
        self.assertIn(".github/workflows/schedule-node.yml",
                      result["changed_workflows_with_new_sites"])

    def test_quoted_node_action_pre_post_keys_are_traversed(self):
        self.put("schedule-lifecycle.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/lifecycle
""")
        self.put_action("lifecycle", """runs:
  using: node20
  main: dist/index.js
  'pre': dist/pre.js
  "post": dist/post.js
""")
        root = self.repo / ".github/actions/lifecycle/dist"
        root.mkdir(parents=True)
        for filename in ("index.js", "pre.js", "post.js"):
            (root / filename).write_text("console.log('safe');\\n", encoding="utf-8")
        base = self.commit()
        (root / "post.js").write_text("github.rest.repos.createDispatchEvent({});\\n",
                                     encoding="utf-8")
        result = guard.evaluate(self.repo, base, self.commit())
        self.assertEqual(result["status"], "reject")



    def test_new_scheduled_yaml_action_alias_is_denied(self):
        self.put("scheduled-alias.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - name: anchor
        env:
          PATH_HINT: &target ./.github/actions/opaque
      - 'uses': *target
""")
        result = self.check(self.commit(), "reject")
        self.assertIn(".github/workflows/scheduled-alias.yml",
                      result["changed_workflows_with_new_sites"])

    def test_new_scheduled_dynamic_action_reference_denied(self):
        self.put("scheduled-expression.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ${{ inputs.action }}
""")
        self.check(self.commit(), "reject")

    def test_unchanged_opaque_scheduled_reference_is_grandfathered(self):
        self.put("scheduled-alias.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: *existing
""")
        base = self.commit()
        result = guard.evaluate(self.repo, base, base)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["new_sites"], 0)

    def test_manual_opaque_action_reference_does_not_add_scheduled_debt(self):
        self.put("manual-alias.yml", """on: workflow_dispatch
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: *existing
""")
        self.check(self.commit(), "pass")


    def test_flow_style_local_reusable_workflow_dispatch_rejected(self):
        self.put("flow-call.yml", """on: schedule
jobs: { call: { uses: ./.github/workflows/helper.yml } }
""")
        self.put("helper.yml", """on: workflow_call
jobs:
  worker:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run production.yml
""")
        result = self.check(self.commit(), "reject")
        self.assertIn(".github/workflows/flow-call.yml",
                      result["changed_workflows_with_new_sites"])

    def test_flow_style_local_composite_action_dispatch_rejected(self):
        self.put("flow-action.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps: [{ 'uses': ./.github/actions/notice }]
""")
        self.put_action("notice", """runs:
  using: composite
  steps:
    - run: gh workflow run production.yml
      shell: bash
""")
        self.check(self.commit(), "reject")

    def test_flow_style_action_main_entrypoint_mutation_rejected(self):
        self.put("flow-main.yml", """on: schedule
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/notice
""")
        self.put_action("notice", """runs: { using: node20, 'main': dist/start.js, pre: dist/pre.js, "post": dist/post.js }
""")
        directory = self.repo / ".github/actions/notice/dist"
        directory.mkdir(parents=True)
        for name in ("start.js", "pre.js", "post.js"):
            (directory / name).write_text("console.log('safe');\\n", encoding="utf-8")
        base = self.commit()
        (directory / "post.js").write_text("github.rest.actions.createWorkflowDispatch({});\\n",
                                            encoding="utf-8")
        result = guard.evaluate(self.repo, base, self.commit())
        self.assertEqual(result["status"], "reject")
        self.assertIn(".github/workflows/flow-main.yml",
                      result["changed_workflows_with_new_sites"])

    def test_unchanged_flow_reusable_graph_is_grandfathered(self):
        self.put("flow-call.yml", """on: schedule
jobs: { call: { uses: ./.github/workflows/helper.yml } }
""")
        self.put("helper.yml", """on: workflow_call
jobs:
  worker:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run old.yml
""")
        base = self.commit()
        result = guard.evaluate(self.repo, base, base)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["new_sites"], 0)


    def test_scheduled_node_commonjs_helper_dispatch_mutation_rejected(self):
        self.put("scheduled-import.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/imports
""")
        self.put_action("imports", """runs:
  using: node20
  main: dist/index.js
""")
        root = self.repo / ".github/actions/imports/dist"
        root.mkdir(parents=True)
        (root / "index.js").write_text("require('./helper.js');\\n", encoding="utf-8")
        helper = root / "helper.js"
        helper.write_text("console.log('safe');\\n", encoding="utf-8")
        base = self.commit()
        self.assertEqual(guard.evaluate(self.repo, base, base)["new_sites"], 0)
        helper.write_text("github.rest.actions.createWorkflowDispatch({});\\n", encoding="utf-8")
        verdict = guard.evaluate(self.repo, base, self.commit())
        self.assertEqual(verdict["status"], "reject")
        self.assertIn(".github/workflows/scheduled-import.yml",
                      verdict["changed_workflows_with_new_sites"])

    def test_scheduled_node_extensionless_require_is_followed(self):
        self.put("scheduled-require.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/require
""")
        self.put_action("require", """runs:
  using: node20
  main: index.js
""")
        root = self.repo / ".github/actions/require"
        (root / "index.js").write_text("require('./dispatch');\\n", encoding="utf-8")
        helper = root / "dispatch.js"
        helper.write_text("console.log('safe');\\n", encoding="utf-8")
        base = self.commit()
        helper.write_text("github.rest.repos.createDispatchEvent({});\\n",
                          encoding="utf-8")
        self.assertEqual(guard.evaluate(self.repo, base, self.commit())["status"], "reject")

    def test_scheduled_node_esm_from_import_is_followed(self):
        self.put("scheduled-esm.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/esm
""")
        self.put_action("esm", """runs:
  using: node20
  main: dist/index.mjs
""")
        root = self.repo / ".github/actions/esm/dist"
        root.mkdir(parents=True)
        (root / "index.mjs").write_text(
            "import { fire } from '../lib/worker.mjs';\\n", encoding="utf-8")
        helper = root.parent / "lib/worker.mjs"
        helper.parent.mkdir()
        helper.write_text("export const fire = () => {};\\n", encoding="utf-8")
        base = self.commit()
        helper.write_text("github.rest.actions.createWorkflowDispatch({});\\n",
                          encoding="utf-8")
        self.assertEqual(guard.evaluate(self.repo, base, self.commit())["status"], "reject")

    def test_scheduled_node_dynamic_literal_import_follows_helper(self):
        self.put("scheduled-dynamic.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/dynamic
""")
        self.put_action("dynamic", """runs:
  using: node20
  main: index.mjs
""")
        root = self.repo / ".github/actions/dynamic"
        (root / "index.mjs").write_text("await import('./helper.mjs');\\n", encoding="utf-8")
        helper = root / "helper.mjs"
        helper.write_text("export const ready = true;\\n", encoding="utf-8")
        base = self.commit()
        helper.write_text("github.rest.repos.createDispatchEvent({});\\n", encoding="utf-8")
        self.assertEqual(guard.evaluate(self.repo, base, self.commit())["status"], "reject")

    def test_recursive_node_import_cycle_is_bounded(self):
        self.put("scheduled-cycle.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/cycle
""")
        self.put_action("cycle", """runs:
  using: node20
  main: index.js
""")
        root = self.repo / ".github/actions/cycle"
        (root / "index.js").write_text("require('./module.js');\\n", encoding="utf-8")
        (root / "module.js").write_text("require('./index.js');\\n", encoding="utf-8")
        base = self.commit()
        result = guard.evaluate(self.repo, base, base)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["new_sites"], 0)

    def test_new_missing_relative_node_import_is_denied(self):
        self.put("scheduled-missing-import.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/missing-import
""")
        self.put_action("missing-import", """runs:
  using: node20
  main: index.js
""")
        (self.repo / ".github/actions/missing-import/index.js").write_text(
            "require('./nonexistent.js');\\n", encoding="utf-8")
        self.check(self.commit(), "reject")

    def test_manual_node_action_import_remains_outside_schedule_gate(self):
        self.put("manual-node.yml", """on: workflow_dispatch
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/manual
""")
        self.put_action("manual", """runs:
  using: node20
  main: index.js
""")
        root = self.repo / ".github/actions/manual"
        (root / "index.js").write_text("require('./helper.js');\\n", encoding="utf-8")
        (root / "helper.js").write_text(
            "github.rest.actions.createWorkflowDispatch({});\\n", encoding="utf-8")
        self.check(self.commit(), "pass")

    def test_scheduled_node_import_outside_action_tree_is_opaque(self):
        self.put("scheduled-escape.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/escape-import
""")
        self.put_action("escape-import", """runs:
  using: node20
  main: index.js
""")
        (self.repo / ".github/actions/escape-import/index.js").write_text(
            "require('../../../scripts/external.js');\\n", encoding="utf-8")
        self.check(self.commit(), "reject")


    def test_new_nonliteral_require_in_scheduled_action_is_denied(self):
        self.put("scheduled-nonliteral.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/dynamic
""")
        self.put_action("dynamic", """runs:
  using: node20
  main: index.js
""")
        root = self.repo / ".github/actions/dynamic"
        (root / "index.js").write_text(
            "const target = process.env.HANDLER; require(target);\\n",
            encoding="utf-8")
        self.check(self.commit(), "reject")

    def test_new_nonliteral_import_in_scheduled_action_is_denied(self):
        self.put("scheduled-variable.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/variable
""")
        self.put_action("variable", """runs:
  using: node20
  main: index.mjs
""")
        root = self.repo / ".github/actions/variable"
        (root / "index.mjs").write_text(
            "await import(process.env.MODULE);\\n", encoding="utf-8")
        self.check(self.commit(), "reject")

    def test_stable_nonliteral_import_is_grandfathered(self):
        self.put("scheduled-old-variable.yml", """on: schedule
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/old-dynamic
""")
        self.put_action("old-dynamic", """runs:
  using: node20
  main: index.js
""")
        (self.repo / ".github/actions/old-dynamic/index.js").write_text(
            "require(process.env.DEPENDENCY);\\n", encoding="utf-8")
        base = self.commit()
        result = guard.evaluate(self.repo, base, base)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["new_sites"], 0)

    def test_unrelated_manual_nonliteral_import_is_not_scanned(self):
        self.put("manual-variable.yml", """on: workflow_dispatch
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/manual-dynamic
""")
        self.put_action("manual-dynamic", """runs:
  using: node20
  main: index.js
""")
        (self.repo / ".github/actions/manual-dynamic/index.js").write_text(
            "require(process.env.DEPENDENCY);\\n", encoding="utf-8")
        self.check(self.commit(), "pass")


if __name__ == "__main__":
    unittest.main()
