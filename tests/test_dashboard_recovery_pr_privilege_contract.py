#!/usr/bin/env python3
"""Static, read-only admission check for dashboard recovery on untrusted PRs.

Runs without network, runner access, secrets or mutation. A failing result is
intentional until the workflow owner closes the #1135 trust boundary.
"""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-dashboard-access-recovery.yml"

def sections(text):
    # Enough for this workflow's top-level event and job keys, without PyYAML.
    event = re.search(r"(?ms)^on:\s*\n(.*?)(?=^\S|\Z)", text)
    jobs = re.search(r"(?ms)^jobs:\s*\n(.*)", text)
    if not event or not jobs:
        raise ValueError("Cannot identify explicit workflow events and jobs; fail closed")
    return event.group(1), jobs.group(1)

class DashboardRecoveryPrivilegeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events, cls.jobs = sections(WORKFLOW.read_text(encoding="utf-8"))

    def test_pr_cannot_reach_mutating_self_hosted_recover(self):
        if re.search(r"(?m)^  pull_request:\s*(?:$|#)", self.events):
            # PR trigger is permissible only when the privileged recovery job is
            # guaranteed skipped. A job-level expression referencing event_name
            # must explicitly exclude pull_request; ambiguous expressions fail.
            recover = re.search(r"(?ms)^  recover:\s*\n(.*?)(?=^  [\w-]+:\s*$|\Z)", self.jobs)
            self.assertIsNotNone(recover, "Unexpected job layout; manual review required")
            body = recover.group(1)
            condition = re.search(r"(?m)^    if:\s*(.+)$", body)
            self.assertIsNotNone(condition, "PR-triggered recover job lacks explicit if guard")
            guard = condition.group(1)
            self.assertRegex(guard, r"github\.event_name")
            self.assertTrue(
                "workflow_dispatch" in guard and "pull_request" not in guard,
                "PR recovery must be explicitly limited to trusted manual dispatch",
            )

    def test_recovery_is_not_implicitly_triggered_on_unreviewed_push(self):
        # Push-triggered privileged repair must similarly be denied, unless a
        # trusted-main exact-SHA admission is independently verified.
        if re.search(r"(?m)^  push:\s*(?:$|#)", self.events):
            recover = re.search(r"(?ms)^  recover:\s*\n(.*?)(?=^  [\w-]+:\s*$|\Z)", self.jobs)
            self.assertIsNotNone(recover)
            guard = re.search(r"(?m)^    if:\s*(.+)$", recover.group(1))
            self.assertIsNotNone(guard, "Push-triggered privileged recovery is unguarded")
            self.assertIn("workflow_dispatch", guard.group(1))

if __name__ == "__main__":
    unittest.main()
