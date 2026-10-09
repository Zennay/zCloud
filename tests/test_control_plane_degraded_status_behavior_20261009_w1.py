"""Behavioral tests: offline status classification never authorizes actions."""
import unittest
from datetime import datetime, timezone, timedelta
from tools.control_plane_degraded_status_offline_20261009_w1 import classify

NOW = datetime(2026, 10, 9, 7, 53, tzinfo=timezone.utc)

def evidence(kind, **kwargs):
    return {"kind": kind, "source": "fixture", "observed_at": NOW.isoformat(), **kwargs}

class ClassifierTests(unittest.TestCase):
    def test_all_labels_non_authorizing(self):
        fixtures = [
            (evidence("api_unavailable"), "Service temporarily unavailable"),
            (evidence("conflict"), "Conflicting observations"),
            (evidence("claim_get"), "Claim state unverified"),
            (evidence("prompt_sent"), "Prompt submitted"),
            (evidence("api_responding"), "API responding"),
            (evidence("generation_started", worker_id="w1", assignment_id="a1", correlated=True), "Generation observed"),
        ]
        for value, expected in fixtures:
            with self.subTest(expected=expected):
                actual = classify(value, now=NOW)
                self.assertEqual(expected, actual["label"])
                self.assertIs(actual["authorizes_action"], False)

    def test_untrusted_or_stale_evidence_fail_closed(self):
        bad = [
            None, {}, [], evidence("generation_started"), evidence("generation_started", worker_id="w1", assignment_id="a1", correlated=1),
            evidence("generation_started", worker_id="w1", assignment_id=""), evidence("api_responding", source=""),
            evidence("api_responding", observed_at="garbage"),
            evidence("api_responding", observed_at="2026-10-09T07:53:00"),
            evidence("api_responding", observed_at=(NOW - timedelta(minutes=3)).isoformat()),
            evidence("api_responding", observed_at=(NOW + timedelta(seconds=1)).isoformat()),
            evidence("missing"),
        ]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual({"label": "Status unknown", "authorizes_action": False}, classify(value, now=NOW))

if __name__ == "__main__":
    unittest.main()
