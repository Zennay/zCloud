"""Static regression: expired privileged rescue entrypoints stay retired.

The 2026-10-09 emergency overnight window has ended. Keep a failed/expired
one-shot workflow from regaining Actions-write, issue-write, or VPS restart
privileges via GitHub's manual dispatch endpoint.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXPIRED = "zcloud-overnight-idle-rescue-20261009.yml"


class ExpiredOvernightRescueRetirementTests(unittest.TestCase):
    def test_privileged_one_shot_is_not_registered(self):
        self.assertFalse((WORKFLOWS / EXPIRED).exists())

    def test_no_other_workflow_still_references_expired_entrypoint(self):
        # The new hosted-only regression workflow intentionally watches the
        # deleted path in pull_request/push 'paths:' filters. That is *not*
        # a workflow_dispatch or recovery target and must remain covered.
        proof = "zcloud-expired-overnight-rescue-retirement-20261012.yml"
        watcher = (WORKFLOWS / proof).read_text(encoding="utf-8")
        self.assertIn('".github/workflows/' + EXPIRED + '"', watcher)
        self.assertIn("runs-on: ubuntu-latest", watcher)
        self.assertIn("contents: read", watcher)
        self.assertNotIn("actions: write", watcher)
        self.assertNotIn("issues: write", watcher)
        self.assertNotIn("workflow_dispatch:", watcher)
        for candidate in sorted(WORKFLOWS.glob("*.y*ml")):
            if candidate.name == proof:
                continue
            with self.subTest(workflow=candidate.name):
                self.assertNotIn(EXPIRED, candidate.read_text(encoding="utf-8"))

    def test_dated_eight_worker_safety_keeps_automatic_trigger_retired(self):
        # This is a separate existing owner scope. Assert the safe observable
        # property; do not modify that workflow or grant a manual restart.
        path = WORKFLOWS / "zcloud-overnight-eight-20261009.yml"
        source = path.read_text(encoding="utf-8")
        self.assertIn("2026-10-09", source)
        self.assertIn("on:\n  workflow_dispatch:", source)
        active_header = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("schedule:", active_header)
        self.assertNotIn("push:", active_header)
        self.assertNotIn("workflow_run:", active_header)

    def test_no_unsafe_retirement_evidence_is_inferred(self):
        # Deleting an expired workflow is NOT proof of active worker continuity
        # or production availability. Keep this fixture explicit in evidence.
        outcome = {
            "dated_entrypoint_retired": not (WORKFLOWS / EXPIRED).exists(),
            "vps_tested": False,
            "workers_restarted": False,
            "production_promoted": False,
        }
        self.assertTrue(outcome["dated_entrypoint_retired"])
        self.assertFalse(outcome["vps_tested"])
        self.assertFalse(outcome["workers_restarted"])
        self.assertFalse(outcome["production_promoted"])


if __name__ == "__main__":
    unittest.main()
