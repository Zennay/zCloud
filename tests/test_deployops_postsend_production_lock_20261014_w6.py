"""Hosted-only contract: post-send source install serializes with zCloud deploy.

The tests never dispatch live jobs, access Firefox, or mutate runner state.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
POSTSEND = ROOT / ".github/workflows/zcloud-postsend-tab-preservation-live.yml"
DEPLOY = ROOT / ".github/workflows/zcloud-vps-deploy.yml"
LOCK = "zcloud-production-deploy"


def errors(postsend: str, deploy: str) -> list[str]:
    problems = []
    if postsend.count("\non:\n") != 1 or postsend.count("\njobs:\n") != 1:
        return ["duplicate_roots"]
    if "\njobs:\n" not in deploy or "\nconcurrency:\n" not in deploy:
        return ["missing_deploy_lock"]
    header, jobs = postsend.split("\njobs:\n", 1)
    if "\n  install:\n" not in jobs or "  offline:\n" not in jobs:
        return ["missing_postsend_jobs"]
    offline = jobs.split("  offline:\n", 1)[1].split("\n  install:\n", 1)[0]
    install = jobs.split("\n  install:\n", 1)[1]
    deploy_group = deploy.split("\nconcurrency:\n", 1)[1].split("\njobs:\n", 1)[0]
    if re.findall(r"(?m)^  group:\s*(\S+)", deploy_group) != [LOCK]:
        problems.append("deploy_group")
    if "cancel-in-progress: false" not in deploy_group:
        problems.append("deploy_non_cancelling")
    top = header.split("\nconcurrency:\n", 1)[1] if "\nconcurrency:\n" in header else ""
    if top.count("group:") != 1 or LOCK in top:
        problems.append("hosted_does_not_lock_prod")
    if "github.event.pull_request.number || github.ref" not in top:
        problems.append("hosted_event_group")
    if "cancel-in-progress: false" not in top:
        problems.append("hosted_non_cancelling")
    admission = install.split("    runs-on:", 1)[0]
    if admission.count("    concurrency:\n") != 1:
        problems.append("one_install_lock")
    lock = admission.split("    concurrency:\n", 1)[1].split("    if:", 1)[0] if "    concurrency:\n" in admission and "    if:" in admission else ""
    if re.findall(r"(?m)^      group:\s*(\S+)", lock) != [LOCK]:
        problems.append("install_uses_prod_lock")
    if re.findall(r"(?m)^      cancel-in-progress:\s*(\S+)", lock) != ["false"]:
        problems.append("install_never_cancels")
    for clause in (
        "github.event_name == 'workflow_dispatch'",
        "github.repository == 'Zennay/zCloud'",
        "github.ref == 'refs/heads/main'",
        "github.actor == 'Zennay'",
        "github.triggering_actor == 'Zennay'",
        "github.run_attempt == 1",
        "inputs.apply == true",
        "inputs.confirm_sha == github.sha",
    ):
        if clause not in admission:
            problems.append("manual_gate:" + clause)
    if "runs-on: [self-hosted, zcloud, vps]" not in install:
        problems.append("permanent_runner")
    if "runs-on: ubuntu-latest" not in offline:
        problems.append("hosted_only")
    if re.search(r"(?m)^\s*(?:sudo|systemctl)\b", offline):
        problems.append("hosted_mutation")
    if "tests.test_deployops_postsend_production_lock_20261014_w6" not in offline:
        problems.append("hosted_test_invoked")
    events = header.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    if events.count("tests/test_deployops_postsend_production_lock_20261014_w6.py") != 2:
        problems.append("test_triggers_both_events")
    return problems


class PostsendProductionLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = POSTSEND.read_text(encoding="utf-8")
        cls.deploy = DEPLOY.read_text(encoding="utf-8")

    def test_current_production_lock_contract(self):
        self.assertEqual([], errors(self.source, self.deploy))

    def test_installer_requires_production_writer_lock(self):
        changed = self.source.replace(
            "      group: zcloud-production-deploy", "      group: isolated-postsend-install", 1)
        self.assertIn("install_uses_prod_lock", errors(changed, self.deploy))

    def test_hosted_pr_must_not_hold_production_writer_lock(self):
        changed = self.source.replace("group: zcloud-postsend-hosted-", "group: zcloud-production-deploy-", 1)
        self.assertIn("hosted_does_not_lock_prod", errors(changed, self.deploy))

    def test_both_writer_operations_must_not_cancel(self):
        changed = self.source.replace("      cancel-in-progress: false",
                                      "      cancel-in-progress: true", 1)
        self.assertIn("install_never_cancels", errors(changed, self.deploy))
        changed_deploy = self.deploy.replace("cancel-in-progress: false",
                                             "cancel-in-progress: true", 1)
        self.assertIn("deploy_non_cancelling", errors(self.source, changed_deploy))

    def test_manual_identity_and_approval_gate_cannot_drift(self):
        for clause in ("github.event_name == 'workflow_dispatch'",
                       "github.ref == 'refs/heads/main'",
                       "github.actor == 'Zennay'",
                       "github.triggering_actor == 'Zennay'",
                       "github.run_attempt == 1",
                       "inputs.apply == true",
                       "inputs.confirm_sha == github.sha"):
            with self.subTest(clause=clause):
                changed = self.source.replace(clause, "false", 1)
                self.assertIn("manual_gate:" + clause, errors(changed, self.deploy))

    def test_hosted_only_never_runs_a_service_command(self):
        changed = self.source.replace(
            "          node --check firefox-extension/background.js",
            "          sudo systemctl restart firefox\n          node --check firefox-extension/background.js", 1)
        self.assertIn("hosted_mutation", errors(changed, self.deploy))

    def test_regression_source_must_trigger_both_events(self):
        changed = self.source.replace(
            "      - tests/test_deployops_postsend_production_lock_20261014_w6.py\n", "", 1)
        self.assertIn("test_triggers_both_events", errors(changed, self.deploy))

    def test_existing_browser_and_live_main_guards_remain(self):
        self.assertIn("require_live_main", self.source)
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = "1"', self.source)
        self.assertIn("BROWSER_SERVICE_SIGNAL_SKIPPED=1", self.source)

if __name__ == "__main__":
    unittest.main()
