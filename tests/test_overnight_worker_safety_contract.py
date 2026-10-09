"""Fail-closed regression checks for the dated zCloud overnight worker watchdog.

These are static guard checks, not evidence that browser generations are healthy.
They deliberately fail against the unsafe 2026-10-09 implementation until the
recovery paths are guarded. Keep this file separate from runtime services.
"""
from pathlib import Path
import unittest

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/zcloud-overnight-eight-20261009.yml"
)


class OvernightWorkerSafetyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_force_push_requires_resource_and_generation_guard(self):
        condition = next(
            line.strip()
            for line in self.source.splitlines()
            if line.strip().startswith('if not state.get("initial_push")')
        )
        self.assertIn("healthy", condition)
        self.assertIn("not active", condition)

    def test_individual_recovery_requires_resource_guard(self):
        section = self.source.split("cooldown=state.setdefault", 1)[1]
        recovery = section.split("summary=f", 1)[0]
        self.assertIn("if healthy", recovery)
        self.assertIn("pending", recovery)
        self.assertIn('"push"', recovery)
        self.assertIn('"new_chat"', recovery)

    def test_runner_live_disconnect_is_fail_closed(self):
        section = self.source.split('live=None', 1)[1]
        self.assertNotIn('rows=((live.get(', section)
        self.assertIn("(live or {})", section)

    def test_backend_recovery_checks_resources(self):
        section = self.source.split("def snapshot():", 1)[1]
        section = section.split("if not firefox():", 1)[0]
        self.assertIn("if healthy:", section)


if __name__ == "__main__":
    unittest.main()
