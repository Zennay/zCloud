"""Offline security contract for exact-SHA deployment status evidence.

Standalone fixture: never contacts GitHub, self-hosted runners, or VPS.
It does NOT enable deployment and is not wired into privileged workflows.
Run: python3 -m unittest discover -s tests -p 'test_deploy_status_sha_payload_offline_20261008_w9.py'
"""
import json
import re
import unittest

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CONTEXT = "zcloud/vps-production"


class AdmissionError(ValueError):
    pass


def production_status_for_candidate(candidate_sha, payload):
    """Validate a commit-status payload before considering its green context."""
    if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
        raise AdmissionError("invalid candidate SHA")
    if not isinstance(payload, dict):
        raise AdmissionError("malformed status response")
    actual = payload.get("sha")
    if not isinstance(actual, str) or not _SHA.fullmatch(actual):
        raise AdmissionError("missing or malformed response SHA")
    if actual != candidate_sha:
        raise AdmissionError("status SHA differs from candidate")
    statuses = payload.get("statuses")
    if not isinstance(statuses, list):
        raise AdmissionError("statuses array missing")
    matches = []
    for entry in statuses:
        if not isinstance(entry, dict) or not isinstance(entry.get("context"), str) or not isinstance(entry.get("state"), str):
            raise AdmissionError("malformed status entry")
        if entry["context"] == _CONTEXT:
            matches.append(entry["state"])
    if len(matches) != 1:
        raise AdmissionError("production status context missing or ambiguous")
    if matches[0] not in {"success", "failure", "pending", "error"}:
        raise AdmissionError("invalid production state")
    return matches[0] == "success"


class ExactShaStatusContractTests(unittest.TestCase):
    SHA = "a" * 40
    OTHER = "b" * 40

    def payload(self, sha=None, statuses=None):
        return {"sha": self.SHA if sha is None else sha,
                "statuses": [{"context": _CONTEXT, "state": "success"}] if statuses is None else statuses}

    def test_matching_green(self):
        self.assertTrue(production_status_for_candidate(self.SHA, self.payload()))

    def test_matching_red(self):
        self.assertFalse(production_status_for_candidate(self.SHA, self.payload(statuses=[{"context": _CONTEXT, "state": "failure"}])))

    def test_stale_green_denied(self):
        with self.assertRaisesRegex(AdmissionError, "differs"):
            production_status_for_candidate(self.SHA, self.payload(sha=self.OTHER))

    def test_missing_response_sha_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, {"statuses": []})

    def test_invalid_candidate_sha_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate("main", self.payload())

    def test_invalid_response_sha_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, self.payload(sha="main"))

    def test_missing_statuses_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, {"sha": self.SHA})

    def test_duplicate_production_context_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, self.payload(statuses=[{"context": _CONTEXT, "state": "success"}] * 2))

    def test_missing_production_context_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, self.payload(statuses=[{"context": "unrelated", "state": "success"}]))

    def test_malformed_entry_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, self.payload(statuses=[None]))

    def test_unknown_state_denied(self):
        with self.assertRaises(AdmissionError):
            production_status_for_candidate(self.SHA, self.payload(statuses=[{"context": _CONTEXT, "state": "green"}]))

    def test_json_roundtrip_is_offline(self):
        self.assertTrue(production_status_for_candidate(self.SHA, json.loads(json.dumps(self.payload()))))


if __name__ == "__main__":
    unittest.main()
