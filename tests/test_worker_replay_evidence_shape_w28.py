"""Offline A-H replay evidence completeness check; never authorizes live recovery.

This only checks the *shape* of manually collected case records. It does not
verify logs, signatures, run authenticity, actual replay, or production safety.
"""
import re
import unittest

CASES = frozenset("ABCDEFGH")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def evidence_complete(candidate_sha, records):
    """Deny by default unless eight distinct exact-head PASS records exist."""
    if not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha):
        return False
    if not isinstance(records, list) or len(records) != 8:
        return False
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            return False
        if set(record) != {"case", "sha", "result", "run_id", "artifact_url"}:
            return False
        case = record["case"]
        if not isinstance(case, str) or case not in CASES or case in seen:
            return False
        if record["sha"] != candidate_sha or record["result"] != "PASS":
            return False
        run_id = record["run_id"]
        url = record["artifact_url"]
        if type(run_id) is not int or run_id <= 0:
            return False
        if not isinstance(url, str) or not url.startswith("https://github.com/") or any(
            ch.isspace() for ch in url
        ):
            return False
        seen.add(case)
    return seen == CASES


def live_recovery_authorized(*_args, **_kwargs):
    """This offline check must never become a deployment authorization."""
    return False


class ReplayEvidenceShapeTests(unittest.TestCase):
    SHA = "a" * 40

    def fixtures(self):
        return [
            dict(case=c, sha=self.SHA, result="PASS", run_id=100 + i,
                 artifact_url=f"https://github.com/Zennay/zCloud/actions/runs/{100+i}")
            for i, c in enumerate("ABCDEFGH")
        ]

    def test_complete_shape_only(self):
        self.assertTrue(evidence_complete(self.SHA, self.fixtures()))

    def test_missing_case(self):
        self.assertFalse(evidence_complete(self.SHA, self.fixtures()[:-1]))

    def test_duplicate_case(self):
        records = self.fixtures()
        records[-1]["case"] = "A"
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_wrong_sha(self):
        records = self.fixtures()
        records[0]["sha"] = "b" * 40
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_failed_case(self):
        records = self.fixtures()
        records[0]["result"] = "FAIL"
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_unknown_case(self):
        records = self.fixtures()
        records[0]["case"] = "I"
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_invalid_sha(self):
        self.assertFalse(evidence_complete("a" * 39, self.fixtures()))
        self.assertFalse(evidence_complete("A" * 40, self.fixtures()))

    def test_invalid_records(self):
        for value in (None, {}, "ABCDEFGH"):
            self.assertFalse(evidence_complete(self.SHA, value))

    def test_no_boolean_run_id(self):
        records = self.fixtures()
        records[0]["run_id"] = True
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_no_missing_artifact(self):
        records = self.fixtures()
        records[0]["artifact_url"] = ""
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_extra_fields_rejected(self):
        records = self.fixtures()
        records[0]["approved"] = True
        self.assertFalse(evidence_complete(self.SHA, records))

    def test_does_not_authorize_live_recovery(self):
        self.assertFalse(live_recovery_authorized(self.SHA, self.fixtures()))


if __name__ == "__main__":
    unittest.main()
