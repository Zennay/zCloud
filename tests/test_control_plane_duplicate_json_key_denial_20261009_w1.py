"""Offline contract: duplicate JSON object keys are ambiguous evidence, never authority.

Reference-only: not connected to production parsing, workers, queues, or dispatch.
"""
import json
import unittest


class AmbiguousEvidence(ValueError):
    pass


def parse_untrusted_evidence(raw: str):
    """Reject repeated keys at every nesting level, including repeated identical values."""
    if not isinstance(raw, str) or len(raw) > 65536:
        raise AmbiguousEvidence("invalid evidence envelope")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AmbiguousEvidence("duplicate evidence field")
            result[key] = value
        return result

    try:
        result = json.loads(
            raw,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                AmbiguousEvidence("nonfinite JSON literal")
            ),
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise AmbiguousEvidence("invalid or ambiguous JSON") from exc
    if not isinstance(result, dict):
        raise AmbiguousEvidence("evidence must be an object")
    return {"parsed": result, "authorizes_action": False}


class DuplicateJSONKeyEvidenceTests(unittest.TestCase):
    def assert_denied(self, raw):
        with self.assertRaises(AmbiguousEvidence):
            parse_untrusted_evidence(raw)

    def test_identical_duplicate_identity_is_denied(self):
        self.assert_denied('{"run_id":123,"run_id":123}')

    def test_conflicting_duplicate_identity_is_denied(self):
        self.assert_denied('{"run_id":123,"run_id":456}')

    def test_nested_override_is_denied(self):
        self.assert_denied('{"worker":{"state":"idle","state":"running"}}')

    def test_duplicate_authority_field_is_denied(self):
        self.assert_denied('{"authorized":false,"authorized":true}')

    def test_nested_list_object_duplicate_is_denied(self):
        self.assert_denied('{"events":[{"attempt":1,"attempt":2}]}')

    def test_nonfinite_literal_denied(self):
        self.assert_denied('{"cpu":NaN}')

    def test_invalid_envelopes_denied(self):
        for raw in ('[]', 'null', '', '{"x":', 42, ' ' * 65537):
            with self.subTest(raw=str(raw)[:20]):
                self.assert_denied(raw)

    def test_unique_object_is_never_authority(self):
        result = parse_untrusted_evidence(
            '{"run_id":123,"events":[{"attempt":1}],"authorized":true}'
        )
        self.assertEqual(result["parsed"]["run_id"], 123)
        self.assertFalse(result["authorizes_action"])


if __name__ == "__main__":
    unittest.main()
