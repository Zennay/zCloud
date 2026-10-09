"""Focused offline contract tests for strict control-plane JSON evidence."""
import unittest

from scripts.control_plane_unique_json_evidence_20261009_w1 import (
    AmbiguousEvidence, parse_unique_evidence,
)


class UniqueEvidenceTests(unittest.TestCase):
    def test_valid_object(self):
        self.assertEqual(parse_unique_evidence('{"action":"pause","state":{"ok":true}}'),
                         {"action": "pause", "state": {"ok": True}})

    def test_duplicate_top_level(self):
        with self.assertRaises(AmbiguousEvidence):
            parse_unique_evidence('{"authorized":false,"authorized":true}')

    def test_duplicate_nested(self):
        with self.assertRaises(AmbiguousEvidence):
            parse_unique_evidence('{"check":{"passed":false,"passed":true}}')

    def test_nested_duplicate_in_array(self):
        with self.assertRaises(AmbiguousEvidence):
            parse_unique_evidence('{"checks":[{"sha":"a","sha":"b"}]}')

    def test_nonfinite(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(AmbiguousEvidence):
                parse_unique_evidence('{"age":' + value + '}')

    def test_non_object(self):
        for raw in ('[]', 'null', '"hello"', 'true'):
            with self.subTest(raw=raw), self.assertRaises(AmbiguousEvidence):
                parse_unique_evidence(raw)

    def test_invalid_json(self):
        with self.assertRaises(AmbiguousEvidence):
            parse_unique_evidence('{"unfinished":')

    def test_bounded_utf8_bytes(self):
        with self.assertRaises(AmbiguousEvidence):
            parse_unique_evidence('{"x":"é"}', max_bytes=8)

    def test_invalid_input_type(self):
        with self.assertRaises(AmbiguousEvidence):
            parse_unique_evidence(None)

    def test_identical_distinct_case_keys(self):
        self.assertEqual(parse_unique_evidence('{"A":1,"a":2}'), {"A": 1, "a": 2})


if __name__ == "__main__":
    unittest.main()
