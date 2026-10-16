"""Real-Git adversarial regressions for scheduled redispatch debt ratchet."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

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

    def test_hosted_only_immutable_pr_checkout(self):
        source = (ROOT / ".github/workflows/zcloud-scheduled-dispatch-debt-guard-20261016-w31.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", source)
        self.assertNotIn("runs-on: [self-hosted", source)
        self.assertNotIn("actions: write", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", source)
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
        cmd(self.repo, "add", ".github/workflows")
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

    def test_shell_continuation_cannot_hide_dispatch(self):
        self.put("new.yml", LEGACY.replace(
            "gh workflow run zcloud-regression-smoke.yml",
            "gh workflow \\\n              run zcloud-regression-smoke.yml"))
        self.check(self.commit(), "reject")

    def test_deletion_of_legacy_site_is_allowed(self):
        self.put("existing.yml", LEGACY.replace("gh workflow run zcloud-regression-smoke.yml", "echo safe"))
        self.check(self.commit(), "pass")

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
