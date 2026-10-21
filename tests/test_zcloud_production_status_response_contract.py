"""Offline contract tests; no GitHub token, runner, or VPS needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from zcloud_production_status_response_contract import (
    InvalidProductionEvidence,
    validate_production_status_payload,
)

SHA = "a" * 40


def item(state="success", context="zcloud/vps-production", stamp="2026-10-08T04:00:00Z", ident=1):
    return {"context": context, "state": state, "created_at": stamp, "id": ident}


class ProductionStatusResponseContractTests(unittest.TestCase):
    def test_exact_head_success(self):
        self.assertEqual("success", validate_production_status_payload(
            {"sha": SHA, "statuses": [item()]}, SHA))

    def test_missing_matching_status_is_not_success(self):
        self.assertEqual("missing", validate_production_status_payload(
            {"sha": SHA, "statuses": [item(context="other")]}, SHA))

    def test_latest_failure_overrides_stale_success(self):
        self.assertEqual("failure", validate_production_status_payload(
            {"sha": SHA, "statuses": [item(), item("failure", stamp="2026-10-08T04:01:00Z", ident=2)]}, SHA))

    def test_status_id_breaks_timestamp_ties(self):
        self.assertEqual("pending", validate_production_status_payload(
            {"sha": SHA, "statuses": [item(), item("pending", ident=2)]}, SHA))

    def test_error_is_not_success(self):
        self.assertEqual("failure", validate_production_status_payload(
            {"sha": SHA, "statuses": [item("error")]}, SHA))

    def test_reject_bad_candidate(self):
        with self.assertRaises(InvalidProductionEvidence):
            validate_production_status_payload({"sha": SHA, "statuses": []}, "main")

    def test_reject_moving_head(self):
        with self.assertRaises(InvalidProductionEvidence):
            validate_production_status_payload({"sha": "b" * 40, "statuses": [item()]}, SHA)

    def test_reject_absent_sha(self):
        with self.assertRaises(InvalidProductionEvidence):
            validate_production_status_payload({"statuses": []}, SHA)

    def test_reject_malformed_statuses(self):
        for statuses in (None, {}, "success", [None], [item(state="unknown")],
                         [item(stamp="")], [item(ident="1")], [item(ident=True)]):
            with self.subTest(statuses=statuses), self.assertRaises(InvalidProductionEvidence):
                validate_production_status_payload({"sha": SHA, "statuses": statuses}, SHA)

    def test_reject_bad_payload(self):
        for payload in (None, [], "ok"):
            with self.subTest(payload=payload), self.assertRaises(InvalidProductionEvidence):
                validate_production_status_payload(payload, SHA)


if __name__ == "__main__":
    unittest.main()
