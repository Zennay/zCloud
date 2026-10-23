from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PushAllWorkersWorkflowTests(unittest.TestCase):
    def test_push_all_requires_material_browser_progress(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn('deadline = time.time() + 60', text)
        self.assertIn('progressed = set()', text)
        self.assertIn('if state.get("status") != "pending":', text)
        self.assertIn('live = call("GET", "/api/runner-live")', text)
        self.assertIn('not runtime["generating"] and not runtime["sending"]', text)
        self.assertIn('fresh push stayed idle after targeted recovery for:', text)
        self.assertIn('MATERIAL_PUSH_PROGRESS=', text)
        self.assertIn('ALL_PENDING_JUSTIFIED_BY_ACTIVE_GENERATION=1', text)
        self.assertIn('ALL_WORKERS_PUSHED_GREEN=1', text)

    def test_push_all_waits_for_desired_allocation_before_dispatch(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn('desired_total = int(', text)
        self.assertIn('allocation_deadline = time.time() + 20', text)
        self.assertIn('ALLOCATION_CONVERGENCE=', text)
        self.assertIn('len(allocation) >= desired_total', text)
        self.assertIn('worker allocation did not converge to desired capacity:', text)
        self.assertLess(
            text.index('ALLOCATION_CONVERGENCE='),
            text.index('FORCE_PUSH='),
        )

    def test_push_all_recovers_only_idle_pending_workers_before_failing(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn('if unjustified_pending:', text)
        self.assertIn('"action": "new_chat"', text)
        self.assertIn('"action": "push"', text)
        self.assertIn('TARGETED_NEW_CHAT=', text)
        self.assertIn('TARGETED_PUSH=', text)
        self.assertIn('TARGETED_RECOVERY_EVIDENCE=', text)
        self.assertIn('recovery_wait_seconds = min(150, max(90, 30 * len(recovery_ids)))', text)
        self.assertIn('TARGETED_NEW_CHAT_WAIT_SECONDS=', text)
        self.assertIn('targeted_wait_seconds = min(120, max(60, 20 * len(targeted_push_ids)))', text)
        self.assertIn('TARGETED_PUSH_WAIT_SECONDS=', text)
        self.assertIn('progressed.update(unjustified_pending)', text)
        self.assertIn('fresh push stayed idle after targeted recovery for:', text)
        self.assertNotIn('restart_firefox', text)

    def test_targeted_recovery_supersedes_only_the_stuck_push_command(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn(
            '"WHERE id=? AND status=\'pending\'"',
            text,
        )
        self.assertIn(
            '"push-all targeted recovery superseded idle pending push"',
            text,
        )
        self.assertNotIn(
            '"UPDATE runner_commands SET status=\'failed\' WHERE status=\'pending\'"',
            text,
        )

    def test_push_all_keeps_failure_and_missing_rows_fail_closed(self):
        text = (ROOT / ".github/workflows/push-all-workers-now.yml").read_text(encoding="utf-8")
        self.assertIn('fresh push failed for:', text)
        self.assertIn('fresh push command missing for:', text)
        self.assertIn('not all allocated workers were queued:', text)


if __name__ == "__main__":
    unittest.main()
