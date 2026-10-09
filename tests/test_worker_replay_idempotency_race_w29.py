"""Offline model test for duplicate no-generation replay triggers.

This deliberately exercises an isolated reference state machine, not the live
userscript. Production acceptance still requires exact-SHA VPS evidence.
Run: python3 -m unittest discover -s tests -p 'test_worker_replay_idempotency_race_w29.py' -v
"""
import itertools
import unittest


class ReplaySlot:
    def __init__(self, assignment):
        self.assignment = assignment
        self.in_flight = False
        self.generation_started = False
        self.submissions = 0
        self.replays = 0

    def trigger(self, assignment):
        if assignment != self.assignment or self.generation_started or self.in_flight:
            return False
        self.in_flight = True
        self.submissions += 1
        self.replays += 1
        return True

    def acknowledge(self):
        # Acknowledgement is NOT generation started.
        return self.in_flight

    def started(self):
        self.generation_started = True
        self.in_flight = False

    def settled_without_generation(self):
        self.in_flight = False


class ReplayRaceContract(unittest.TestCase):
    def test_duplicate_timer_and_manual_triggers_are_single_flight(self):
        for order in itertools.permutations(("deadline", "retry", "reconnect")):
            with self.subTest(order=order):
                slot = ReplaySlot("assignment-A")
                outcomes = [slot.trigger("assignment-A") for _ in order]
                self.assertEqual(outcomes, [True, False, False])
                self.assertEqual(slot.submissions, 1)

    def test_ack_does_not_open_second_submission(self):
        slot = ReplaySlot("assignment-A")
        self.assertTrue(slot.trigger("assignment-A"))
        self.assertTrue(slot.acknowledge())
        self.assertFalse(slot.trigger("assignment-A"))
        self.assertEqual(slot.submissions, 1)

    def test_generation_start_cancels_all_late_replay_triggers(self):
        slot = ReplaySlot("assignment-A")
        slot.trigger("assignment-A")
        slot.started()
        slot.settled_without_generation()
        for _ in range(10):
            self.assertFalse(slot.trigger("assignment-A"))
        self.assertEqual(slot.submissions, 1)

    def test_different_assignment_cannot_reuse_slot(self):
        slot = ReplaySlot("assignment-A")
        self.assertFalse(slot.trigger("assignment-B"))
        self.assertTrue(slot.trigger("assignment-A"))
        slot.settled_without_generation()
        self.assertFalse(slot.trigger("assignment-B"))

    def test_serial_retry_requires_settlement(self):
        slot = ReplaySlot("assignment-A")
        self.assertTrue(slot.trigger("assignment-A"))
        self.assertFalse(slot.trigger("assignment-A"))
        slot.settled_without_generation()
        self.assertTrue(slot.trigger("assignment-A"))
        self.assertEqual(slot.submissions, 2)
        self.assertFalse(slot.in_flight is False)


if __name__ == "__main__":
    unittest.main()
