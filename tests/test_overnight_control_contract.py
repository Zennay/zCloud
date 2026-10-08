#!/usr/bin/env python3
"""Static regression guards for dated zCloud worker-recovery workflows.

Read-only: never contacts the VPS, GitHub API or browser. Run with:
    python3 -m unittest discover -s tests -p 'test_overnight_control_contract.py'
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / ".github" / "workflows"
WATCH = ROOT / "zcloud-overnight-eight-20261009.yml"
ONESHOT = ROOT / "owner-one-shot-worker-restart-20261009.yml"


class OvernightControlContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watch = WATCH.read_text(encoding="utf-8")
        cls.oneshot = ONESHOT.read_text(encoding="utf-8")

    def test_recovery_uses_trusted_vps_runner(self):
        for name, source in (("watchdog", self.watch), ("one-shot", self.oneshot)):
            with self.subTest(name=name):
                self.assertRegex(source, r"runs-on:\s*\[self-hosted,\s*zcloud,\s*vps\]")
                self.assertIn("timeout-minutes:", source)
                self.assertNotIn("pull_request:", source)
                self.assertNotIn("pull_request_target:", source)

    def test_watchdog_is_date_bounded_and_nonoverlapping(self):
        self.assertIn('local.date().isoformat()!="2026-10-09"', self.watch)
        self.assertIn("local.hour>=8", self.watch)
        self.assertIn('cancel-in-progress: false', self.watch)
        self.assertIn("zcloud-overnight-eight-20261009.yml/disable", self.watch)
        self.assertIn("STATE.write_text", self.watch)

    def test_no_post_retry_or_unbounded_recovery_loop(self):
        self.assertIn('attempts=4 if (method=="GET" and not gh) else 1', self.watch)
        self.assertIn("if time.time()-float(cooldown.get(key,0))<600: continue", self.watch)
        self.assertIn("deadline = time.time() + 160", self.oneshot)
        self.assertRegex(self.oneshot, r"while time\.time\(\) < deadline:")

    def test_one_shot_preserves_allocation_and_checks_command_results(self):
        self.assertIn('before = targets()', self.oneshot)
        self.assertIn('after = targets()', self.oneshot)
        self.assertIn('current.get(key) != previous.get(key)', self.oneshot)
        self.assertIn('not response.get("command_id")', self.oneshot)
        self.assertIn('"pending"', self.oneshot)
        self.assertIn('"completed"', self.oneshot)

    def test_watchdog_does_not_use_force_push_before_pending_command_check(self):
        pending = self.watch.index("SELECT project_id,action FROM runner_commands")
        conflict = self.watch.index("if conflicting:")
        force = self.watch.index('"/api/dynamic-workers/force-push"')
        self.assertLess(pending, conflict)
        self.assertLess(conflict, force)
        self.assertIn("FORCE_PUSH_DEFERRED_PENDING=", self.watch)


if __name__ == "__main__":
    unittest.main()
