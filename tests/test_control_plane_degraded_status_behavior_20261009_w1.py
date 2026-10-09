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
                actual = classify(value, now=NOW, expected_worker_id='w1', expected_assignment_id='a1')
                self.assertEqual(expected, actual["label"])
                self.assertIs(actual["authorizes_action"], False)

    def test_untrusted_or_stale_evidence_fail_closed(self):
        bad = [
            None, {}, [], evidence("generation_started"), evidence("generation_started", worker_id="w1", assignment_id="a1", correlated=1),
            evidence("generation_started", worker_id="w1", assignment_id=""), evidence("api_responding", source=""),
            evidence("api_responding", observed_at="garbage"),
            evidence("api_responding", observed_at="2026-10-09T07:53:00"),
            evidence("api_responding", observed_at="2026-10-09T09:53:00+02:00"),
            evidence("api_responding", observed_at=(NOW - timedelta(minutes=3)).isoformat()),
            evidence("api_responding", observed_at=(NOW + timedelta(seconds=1)).isoformat()),
            evidence("missing"),
        ]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual({"label": "Status unknown", "authorizes_action": False}, classify(value, now=NOW))


    def test_forged_authority_flags_never_grant_permission(self):
        for kind in ("generation_started", "api_responding", "prompt_sent", "api_unavailable"):
            item = evidence(kind, worker_id="worker-1", assignment_id="a-1",
                            correlated=True, authorizes_action=True,
                            restart_authorized=True, deploy_authorized=True)
            with self.subTest(kind=kind):
                actual = classify(item, now=NOW)
                self.assertIs(actual["authorizes_action"], False)
                self.assertEqual({"label", "authorizes_action"}, set(actual))

    def test_invalid_current_clock_fails_closed(self):
        for now in (None, "2026-10-09", datetime(2026, 10, 9, 7, 53),
                    datetime(2026, 10, 9, 9, 53, tzinfo=timezone(timedelta(hours=2)))):
            with self.subTest(now=now):
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(evidence("api_responding"), now=now))


    def test_generation_requires_external_identity_binding(self):
        forged = evidence("generation_started", worker_id="w1", assignment_id="a1", correlated=True)
        for kwargs in ({}, {"expected_worker_id": "w1"}, {"expected_assignment_id": "a1"},
                       {"expected_worker_id": "w2", "expected_assignment_id": "a1"},
                       {"expected_worker_id": "w1", "expected_assignment_id": "a2"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual("Status unknown", classify(forged, now=NOW, **kwargs)["label"])
        self.assertEqual("Generation observed", classify(
            forged, now=NOW, expected_worker_id="w1", expected_assignment_id="a1")["label"])


    def test_unknown_observation_keys_fail_closed(self):
        for key, value in (("status", "running"), ("authorizes_action", True),
                           ("restart_authorized", True), ("producer_verified", True)):
            with self.subTest(key=key):
                forged = evidence("generation_started", worker_id="w1", assignment_id="a1",
                                  correlated=True, **{key: value})
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(forged, now=NOW, expected_worker_id="w1",
                                          expected_assignment_id="a1"))

if __name__ == "__main__":
    unittest.main()
