#!/usr/bin/env python3
"""Non-mutating synthetic proof for zCloud safe-idle deploy drain failure logs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "scripts/zcloud_safe_idle_blocker_evidence_20261013_w1.py"
spec = importlib.util.spec_from_file_location("drain_evidence", FILE)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
PREFIX = ("2026-10-13T21:48:06.0879606Z ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED: "
          "browser workers did not reach safe-idle before deployment timeout; ")


def row(worker="supa::w1", *, generating=True, sending=False,
        status="completed", desired="draining", age=2.4):
    return {
        "worker_id": worker, "desired_state": desired,
        "drain_command_status": status, "drain_command_id": 123,
        "generating": generating, "sending": sending,
        "last_event": "heartbeat", "last_event_age_seconds": age,
    }


def log(*rows):
    return "non-sensitive job setup\n" + PREFIX + json.dumps(list(rows), separators=(",", ":")) + "\n"


class SafeIdleEvidenceTests(unittest.TestCase):
    def test_real_sqlite_readonly_marker_not_conflated_with_generating_timeout(self):
        raw = ("2026-10-13T18:10:48.9881315Z " + evidence.SQLITE_READONLY_MARKER
               + "\n")
        at = datetime(2026, 10, 13, 18, 11, tzinfo=timezone.utc)
        record = evidence.classify(raw, now=at)
        self.assertEqual(record["failure_category"], "sqlite_readonly_error_marker")
        self.assertIsNone(record["blocker_count"])
        self.assertIsNone(record["classes"])
        self.assertFalse(record["only_acknowledged_draining_generation"])
        self.assertFalse(record["independent_live_worker_state_verified"])
        self.assertFalse(record["authorizes_mutation"])

    def test_mixed_sqlite_and_generation_markers_are_ambiguous(self):
        raw = ("2026-10-13T18:10:48.9881315Z " + evidence.SQLITE_READONLY_MARKER
               + "\n" + log(row()))
        with self.assertRaisesRegex(evidence.UntrustedLog,
                                    "ambiguous_or_missing_safe_idle_marker"):
            evidence.classify(raw)

    def test_sqlite_marker_future_timestamp_rejected(self):
        raw = "2099-10-13T18:10:48Z " + evidence.SQLITE_READONLY_MARKER + "\n"
        with self.assertRaisesRegex(evidence.UntrustedLog, "future_log_timestamp"):
            evidence.classify(raw)

    def test_unhashable_drain_and_desired_states_are_safely_denied(self):
        for value in ({}, [], {"fake": "completed"}, True, 2):
            for attribute, reason in (("desired_state", "invalid_desired_state"),
                                      ("drain_command_status", "invalid_drain_status")):
                with self.subTest(value=value, attribute=attribute):
                    sample = row()
                    sample[attribute] = value
                    with self.assertRaisesRegex(evidence.UntrustedLog, reason):
                        evidence.classify(log(sample))

    def test_nonfinite_or_surrogate_data_in_ignored_fields_denied(self):
        samples = [
            '{"worker_id":"supa::w1","ignored":1e999}',
            '{"worker_id":"supa::w1","ignored":"\\ud800"}',
            '{"worker_id":"supa::w1","ignored":{"x":NaN}}',
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                with self.assertRaises(evidence.UntrustedLog):
                    evidence._validated_marker_payload("[" + raw + "]")

    def test_deep_nested_untrusted_extras_are_bounded(self):
        raw = '{"worker_id":"supa::w1","opaque":' + "[" * 35 + "0" + "]" * 35 + "}"
        with self.assertRaisesRegex(evidence.UntrustedLog, "invalid_blocker_structure"):
            evidence._validated_marker_payload("[" + raw + "]")

    def test_completed_drain_but_generation_active_is_not_success(self):
        result = evidence.classify(log(row(), row("supa::w2"), row("zguard::w1")))
        self.assertEqual(result["blocker_count"], 3)
        self.assertEqual(result["classes"]["generating_after_acknowledged_drain"], 3)
        self.assertTrue(result["only_acknowledged_draining_generation"])
        self.assertFalse(result["proves_browser_text_progress"])
        self.assertFalse(result["proves_process_stuck"])
        self.assertFalse(result["authorizes_mutation"])
        self.assertFalse(result["production_deploy_verified"])
        self.assertNotIn("supa", str(result))
        self.assertNotIn("zguard", str(result))

    def test_pending_drain_is_not_same_as_completed_drain(self):
        result = evidence.classify(log(row(status="pending")))
        self.assertEqual(result["classes"]["draining_without_completed_acknowledgement"], 1)
        self.assertFalse(result["only_acknowledged_draining_generation"])

    def test_mixed_drain_distinguishes_other_active_signals(self):
        result = evidence.classify(log(row(), row("ftmo::w1", desired="running", sending=True)))
        self.assertEqual(result["classes"]["generating_after_acknowledged_drain"], 1)
        self.assertEqual(result["classes"]["other_active_blocker"], 1)
        self.assertFalse(result["only_acknowledged_draining_generation"])

    def test_empty_or_duplicate_worker_ids_fail_closed(self):
        for worker in ("", "not-a-worker", "supa::w0", "supa::w999"):
            with self.subTest(worker=worker):
                with self.assertRaises(evidence.UntrustedLog):
                    evidence.classify(log(row(worker)))
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(log(row(), row()))

    def test_idle_worker_cannot_be_silently_counted_as_active(self):
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(log(row(generating=False, sending=False)))

    def test_invalid_clock_and_types_denied(self):
        for changes in ({"age": -1}, {"age": 3601}, {"age": True},
                        {"status": "unknown"}, {"desired": "mystery"}):
            with self.subTest(changes=changes):
                with self.assertRaises(evidence.UntrustedLog):
                    evidence.classify(log(row(**changes)))

    def test_future_or_malformed_log_header_does_not_match(self):
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(evidence.MARKER + json.dumps([row()]) + "\n")
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify("not a safe idle trace")

    def test_future_timestamp_receipt_is_not_trusted(self):
        raw = ("2099-10-13T21:48:06.0879606Z " + evidence.MARKER
               + json.dumps([row()]) + "\n")
        with self.assertRaisesRegex(evidence.UntrustedLog, "future_log_timestamp"):
            evidence.classify(raw)

    def test_historic_marker_never_claims_live_worker_state(self):
        now = datetime(2026, 10, 13, 22, 15, tzinfo=timezone.utc)
        data = evidence.classify(log(row()), now=now)
        self.assertGreater(data["marker_age_seconds"], 900)
        self.assertFalse(data["fresh_for_triage"])
        self.assertFalse(data["independent_live_worker_state_verified"])
        self.assertFalse(data["authorizes_worker_interrupt"])

    def test_fresh_marker_is_triage_only_not_live_worker_proof(self):
        now = datetime(2026, 10, 13, 21, 48, 15, tzinfo=timezone.utc)
        data = evidence.classify(log(row()), now=now)
        self.assertTrue(data["fresh_for_triage"])
        self.assertLess(data["marker_age_seconds"], 900)
        self.assertFalse(data["independent_live_worker_state_verified"])
        self.assertFalse(data["production_deploy_verified"])

    def test_invalid_or_naive_observation_clock_fails_closed(self):
        for clock in ("untrusted", datetime(2026, 10, 13, 21, 48, 15)):
            with self.subTest(clock=clock):
                with self.assertRaisesRegex(evidence.UntrustedLog,
                                            "invalid_observation_clock"):
                    evidence.classify(log(row()), now=clock)
        instant = datetime(2026, 10, 13, 21, 48, tzinfo=timezone.utc)
        with self.assertRaisesRegex(evidence.UntrustedLog,
                                    "future_log_timestamp"):
            evidence.classify(log(row()), now=instant - timedelta(days=1))

    def test_double_receipt_denied_even_when_both_valid(self):
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(log(row()) + log(row("ftmo::w1")))

    def test_duplicate_json_key_and_nonfinite_rejected(self):
        raw = PREFIX + '[{"worker_id":"supa::w1","worker_id":"supa::w2"}]' + "\n"
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(raw)
        raw2 = PREFIX + '[{"worker_id":"supa::w1","generating":NaN}]' + "\n"
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(raw2)

    def test_secret_text_surrounding_marker_is_not_reported(self):
        text = "SECRET_TOKEN_DO_NOT_PRINT\n" + log(row())
        self.assertNotIn("SECRET_TOKEN_DO_NOT_PRINT", str(evidence.classify(text)))

    def test_oversize_log_and_marker_denied(self):
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify("x" * (evidence.MAX_LOG_BYTES + 1))
        with self.assertRaises(evidence.UntrustedLog):
            evidence.classify(PREFIX + "[" + " " * evidence.MAX_MARKER_BYTES + "]" + "\n")

    def test_parses_real_github_timestamp_prefix(self):
        result = evidence.classify(
            "2026-10-13T21:48:06.0879606Z "
            + evidence.MARKER
            + json.dumps([row("cloud::w1")]) + "\n")
        self.assertEqual(result["blocker_count"], 1)

    def test_no_mutating_source_or_api_request(self):
        code = FILE.read_text(encoding="utf-8")
        self.assertNotIn("urllib.request", code)
        self.assertNotIn("subprocess", code)
        self.assertNotIn("systemctl", code)
        self.assertNotIn("POST", code)
        self.assertIn('"authorizes_mutation": False', code)


if __name__ == "__main__":
    unittest.main()
