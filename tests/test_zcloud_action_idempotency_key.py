"""Offline invariants for the advisory action identity contract."""
import unittest

from scripts.zcloud_action_idempotency_key import (
    InvalidActionIdentity,
    action_idempotency_key,
)

BASE = dict(project="cloud", worker="worker_1", action="push", request_id="request_abcdefghij12345")


class ActionIdempotencyKeyTests(unittest.TestCase):
    def test_same_intent_is_stable(self):
        self.assertEqual(action_idempotency_key(**BASE), action_idempotency_key(**BASE))

    def test_different_intents_do_not_collapse(self):
        initial = action_idempotency_key(**BASE)
        for field, value in (
            ("project", "ftmo"),
            ("worker", "worker_2"),
            ("action", "pause"),
            ("request_id", "request_abcdefghij12346"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(initial, action_idempotency_key(**{**BASE, field: value}))

    def test_output_is_opaque_and_bounded(self):
        value = action_idempotency_key(**BASE)
        self.assertRegex(value, r"^zca1_[0-9a-f]{64}$")
        self.assertNotIn(BASE["request_id"], value)

    def test_rejects_noncanonical_untrusted_values(self):
        for field, bad in (
            ("project", "../cloud"), ("project", "Cloud"), ("project", ""),
            ("worker", "worker 1"), ("action", "delete"), ("action", "PUSH"),
            ("request_id", "short"), ("request_id", "a" * 129),
            ("request_id", "unsafe/token"), ("worker", None),
            ("action", ["push"]),
        ):
            with self.subTest(field=field, bad=bad):
                with self.assertRaises(InvalidActionIdentity):
                    action_idempotency_key(**{**BASE, field: bad})


if __name__ == "__main__":
    unittest.main()
