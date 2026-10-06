import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_dead_conversation_cleanup_selector as selector


NOW = datetime(2026, 10, 6, 22, 35, tzinfo=timezone.utc)


def record(**overrides):
    value = {
        "project_id": "cloud",
        "worker_slot": 2,
        "provider": "chatgpt",
        "conversation_id": "12345678-1234-1234-1234-123456789abc",
        "tombstone_reason": "worker_slot_retired",
        "tombstoned_at": "2026-08-01T12:00:00Z",
        "project_present": True,
        "target_active": False,
        "worker_allocated": False,
        "active_claim": False,
        "pending_command": False,
        "browser_session_bound": False,
        "recovery_pending": False,
        "replacement_handoff_pending": False,
        "reference_count": 0,
        "durable_tombstone_receipt": True,
    }
    value.update(overrides)
    return value


class DeadConversationCleanupSelectorTests(unittest.TestCase):
    def test_old_tombstoned_unreferenced_binding_is_candidate(self):
        result = selector.select([record()], now=NOW)
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(0, result["blocked_count"])
        self.assertFalse(result["mutation_performed"])

    def test_blocks_each_liveness_and_recovery_signal(self):
        cases = [
            ("active_target", {"target_active": True}, "TARGET_ACTIVE"),
            ("allocated", {"worker_allocated": True}, "WORKER_ALLOCATED"),
            ("claim", {"active_claim": True}, "ACTIVE_CLAIM"),
            ("command", {"pending_command": True}, "PENDING_COMMAND"),
            ("browser", {"browser_session_bound": True}, "BROWSER_SESSION_BOUND"),
            ("recovery", {"recovery_pending": True}, "RECOVERY_PENDING"),
            (
                "handoff",
                {"replacement_handoff_pending": True},
                "REPLACEMENT_HANDOFF_PENDING",
            ),
            ("reference", {"reference_count": 1}, "REFERENCED"),
            (
                "receipt",
                {"durable_tombstone_receipt": False},
                "NO_DURABLE_TOMBSTONE",
            ),
            (
                "recent",
                {"tombstoned_at": "2026-10-01T00:00:00Z"},
                "TOMBSTONE_TOO_RECENT",
            ),
        ]
        for label, overrides, expected in cases:
            with self.subTest(label=label):
                result = selector.select([record(**overrides)], now=NOW)
                self.assertEqual(0, result["candidate_count"])
                self.assertIn(expected, result["blocked"][0]["reasons"])

    def test_retirement_reason_must_match_project_presence(self):
        removed = selector.select(
            [
                record(
                    tombstone_reason="project_removed",
                    project_present=True,
                )
            ],
            now=NOW,
        )
        self.assertIn("PROJECT_STILL_PRESENT", removed["blocked"][0]["reasons"])

        local = selector.select(
            [
                record(
                    tombstone_reason="conversation_reset_confirmed",
                    project_present=False,
                )
            ],
            now=NOW,
        )
        self.assertIn(
            "PROJECT_MISSING_FOR_LOCAL_RETIREMENT",
            local["blocked"][0]["reasons"],
        )

        valid_removed = selector.select(
            [
                record(
                    tombstone_reason="project_removed",
                    project_present=False,
                )
            ],
            now=NOW,
        )
        self.assertEqual(1, valid_removed["candidate_count"])

    def test_duplicate_binding_is_blocked_fail_closed(self):
        result = selector.select(
            [
                record(project_id="cloud", worker_slot=2),
                record(project_id="ftmo", worker_slot=1),
            ],
            now=NOW,
        )
        self.assertEqual(0, result["candidate_count"])
        self.assertEqual(2, result["blocked_count"])
        for item in result["blocked"]:
            self.assertIn("DUPLICATE_BINDING", item["reasons"])

    def test_rejects_unknown_fields_loose_types_and_hidden_mutation_controls(self):
        with self.assertRaises(selector.InputError):
            selector.select([{**record(), "delete": True}], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select({**{"conversations": [record()]}, "apply": True}, now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([record(active_claim=0)], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([record(reference_count=False)], now=NOW)

    def test_rejects_invalid_identifiers_future_time_and_oversized_inventory(self):
        with self.assertRaises(selector.InputError):
            selector.select([record(project_id="../cloud")], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([record(conversation_id="bad/id")], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([record(tombstoned_at="2026-10-07T00:00:00Z")], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select(
                [
                    record(
                        conversation_id=f"conversation-{i:04d}",
                        worker_slot=(i % selector.MAX_WORKER_SLOT) + 1,
                    )
                    for i in range(selector.MAX_RECORDS + 1)
                ],
                now=NOW,
            )

    def test_load_payload_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "payload.json"
            target.write_text(json.dumps([record()]), encoding="utf-8")
            link = root / "payload-link.json"
            os.symlink(target, link)
            with self.assertRaises(selector.InputError):
                selector.load_payload(link)

    def test_candidate_order_is_deterministic(self):
        result = selector.select(
            [
                record(
                    project_id="supa",
                    worker_slot=3,
                    conversation_id="conversation-supa-old",
                    tombstoned_at="2026-07-01T00:00:00Z",
                ),
                record(
                    project_id="cloud",
                    worker_slot=2,
                    conversation_id="conversation-cloud-b",
                    tombstoned_at="2026-08-01T00:00:00Z",
                ),
                record(
                    project_id="cloud",
                    worker_slot=1,
                    conversation_id="conversation-cloud-a",
                    tombstoned_at="2026-08-01T00:00:00Z",
                ),
            ],
            now=NOW,
        )
        self.assertEqual(
            [
                "conversation-supa-old",
                "conversation-cloud-a",
                "conversation-cloud-b",
            ],
            [item["conversation_id"] for item in result["candidates"]],
        )


if __name__ == "__main__":
    unittest.main()
