#!/usr/bin/env python3
"""Non-mutating synthetic proof for zCloud safe-idle deploy drain failure logs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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
