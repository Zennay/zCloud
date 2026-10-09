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


    def test_nonstring_observation_keys_fail_closed(self):
        for key in (1, None, ("kind",), False):
            with self.subTest(key=key):
                value = evidence("api_responding")
                value[key] = "spoofed"
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(value, now=NOW))


    def test_generation_identity_fields_require_exact_strings(self):
        for worker, assignment in ((1, "a1"), ("w1", 1), (None, "a1"),
                                   ("w1", None), (["w1"], "a1"), ("w1", ["a1"])):
            with self.subTest(worker=worker, assignment=assignment):
                item = evidence("generation_started", worker_id=worker,
                                assignment_id=assignment, correlated=True)
                self.assertEqual("Status unknown", classify(
                    item, now=NOW, expected_worker_id="w1",
                    expected_assignment_id="a1")["label"])


    def test_per_kind_schema_blocks_identity_smuggling(self):
        for kind in ("prompt_sent", "api_responding", "api_unavailable", "conflict", "claim_get"):
            with self.subTest(kind=kind):
                item = evidence(kind, worker_id="w1", assignment_id="a1", correlated=True)
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(item, now=NOW, expected_worker_id="w1", expected_assignment_id="a1"))

    def test_oversize_string_fields_fail_closed(self):
        for field in ("kind", "source", "observed_at", "worker_id", "assignment_id"):
            with self.subTest(field=field):
                item = evidence("generation_started", worker_id="w1", assignment_id="a1", correlated=True)
                item[field] = "X" * 257
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(item, now=NOW, expected_worker_id="w1", expected_assignment_id="a1"))


    def test_correlation_requires_boolean_even_for_denied_generation(self):
        for value in (1, 0, "true", None, [], {}):
            with self.subTest(value=value):
                item = evidence("generation_started", worker_id="w1", assignment_id="a1",
                                correlated=value)
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(item, now=NOW, expected_worker_id="w1",
                                          expected_assignment_id="a1"))


    def test_whitespace_ambiguous_sources_and_expected_ids_fail_closed(self):
        for source in (" fixture", "fixture ", " fixture "):
            with self.subTest(source=source):
                self.assertEqual("Status unknown", classify(
                    evidence("api_responding", source=source), now=NOW)["label"])
        item = evidence("generation_started", worker_id="w1", assignment_id="a1", correlated=True)
        for expected_worker, expected_assignment in ((" w1", "a1"), ("w1 ", "a1"), ("w1", " a1")):
            with self.subTest(expected_worker=expected_worker, expected_assignment=expected_assignment):
                self.assertEqual("Status unknown", classify(item, now=NOW,
                    expected_worker_id=expected_worker, expected_assignment_id=expected_assignment)["label"])


    def test_source_control_characters_are_denied(self):
        for source in ("source\x00", "source\x1b", "source\n", "source\x7f", "source\x85"):
            with self.subTest(source=repr(source)):
                self.assertEqual({"label": "Status unknown", "authorizes_action": False},
                                 classify(evidence("api_responding", source=source), now=NOW))


    def test_control_characters_in_generation_ids_fail_closed(self):
        for name in ("worker_id", "assignment_id"):
            for character in map(chr, (0, 27, 127, 133)):
                with self.subTest(name=name, character=character):
                    values = {"worker_id": "w1", "assignment_id": "a1", "correlated": True}
                    values[name] += character
                    item = evidence("generation_started", **values)
                    self.assertEqual("Status unknown", classify(
                        item, now=NOW, expected_worker_id=values["worker_id"],
                        expected_assignment_id=values["assignment_id"])["label"])


    def test_unicode_format_chars_are_not_trusted_in_identifiers(self):
        for marker in map(chr, (0x200B, 0x200D, 0x202E, 0x2060)):
            with self.subTest(codepoint=ord(marker)):
                self.assertEqual("Status unknown", classify(
                    evidence("api_responding", source="source" + marker), now=NOW)["label"])
                values = {"worker_id": "w1" + marker, "assignment_id": "a1", "correlated": True}
                self.assertEqual("Status unknown", classify(
                    evidence("generation_started", **values), now=NOW,
                    expected_worker_id=values["worker_id"],
                    expected_assignment_id="a1")["label"])


    def test_observed_identity_whitespace_fails_even_if_expected_matches(self):
        for field in ("worker_id", "assignment_id"):
            for suffix in (" ", chr(9), chr(10)):
                with self.subTest(field=field, suffix=suffix):
                    values = {"worker_id": "w1", "assignment_id": "a1", "correlated": True}
                    values[field] += suffix
                    self.assertEqual("Status unknown", classify(
                        evidence("generation_started", **values), now=NOW,
                        expected_worker_id=values["worker_id"],
                        expected_assignment_id=values["assignment_id"])["label"])

if __name__ == "__main__":
    unittest.main()
