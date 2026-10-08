import datetime as dt
import importlib.util
import pathlib
import tempfile
import os
import json
import unittest
from unittest import mock

PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_receipt_clock_skew_audit.py"
spec = importlib.util.spec_from_file_location("clock_audit", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = dt.datetime(2026, 10, 8, 1, 0, tzinfo=dt.timezone.utc)

class ClockSkewAuditTests(unittest.TestCase):
    def check(self, payload, **kwargs):
        result = module.evaluate(payload, now=NOW, **kwargs)
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["merge_authorized"])
        self.assertFalse(result["mutation_performed"])
        return result

    def test_fresh_is_consistency_only(self):
        self.assertEqual(self.check({"observed_at": "2026-10-08T00:59:00Z"})["reason"], "CLOCK_CONSISTENT_ONLY")

    def test_future_rejected(self):
        self.assertEqual(self.check({"observed_at": "2026-10-08T01:02:00Z"})["reason"], "FUTURE_TIMESTAMP")

    def test_stale_rejected(self):
        self.assertEqual(self.check({"observed_at": "2026-10-08T00:40:00Z"})["reason"], "STALE_TIMESTAMP")

    def test_missing_and_extra_fields_rejected(self):
        for payload in ({}, {"observed_at": "2026-10-08T01:00:00Z", "secret": "never echo"}, []):
            with self.subTest(payload=payload):
                self.assertEqual(self.check(payload)["reason"], "INVALID_RECEIPT")

    def test_non_utc_and_invalid_rejected(self):
        for stamp in ("2026-10-08T01:00:00+00:00", "nonsense", 123, "2026-10-08T01:00:00+01:00"):
            with self.subTest(stamp=stamp):
                self.assertEqual(self.check({"observed_at": stamp})["reason"], "INVALID_RECEIPT")

    def test_noncanonical_utc_stamps_rejected(self):
        for stamp in ("2026-10-08 01:00:00Z", "2026-10-08T01:00Z",
                      "2026-10-08T01:00:00.1234567Z"):
            with self.subTest(stamp=stamp):
                self.assertEqual(self.check({"observed_at": stamp})["reason"], "INVALID_RECEIPT")

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "duplicate.json"
            path.write_text('{"observed_at":"2026-10-08T01:00:00Z","observed_at":"2026-10-08T01:00:00Z"}')
            with mock.patch("sys.argv", ["clock-audit", str(path)]):
                with mock.patch("sys.stdout"):
                    self.assertEqual(module.main(), 1)

    def test_policy_bounds(self):
        self.assertEqual(self.check({"observed_at": "2026-10-08T01:00:00Z"}, future_skew_seconds=999)["reason"], "INVALID_RECEIPT")
        self.assertEqual(self.check({"observed_at": "2026-10-08T01:00:00Z"}, max_age_seconds=-1)["reason"], "INVALID_RECEIPT")

    def test_cli_rejects_oversized_and_symlinked_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            large = pathlib.Path(temp) / "large.json"
            large.write_text("x" * 4097)
            link = pathlib.Path(temp) / "link.json"
            link.symlink_to(large)
            for path in (large, link):
                with self.subTest(path=str(path)):
                    with mock.patch("sys.argv", ["clock-audit", str(path)]):
                        with mock.patch("sys.stdout"):
                            self.assertEqual(module.main(), 1)

    def test_cli_fresh_file_is_advisory_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "receipt.json"
            path.write_text(json.dumps({"observed_at": "2026-10-08T01:00:00Z"}))
            with mock.patch("sys.argv", ["clock-audit", str(path)]):
                with mock.patch.object(module.dt, "datetime", wraps=dt.datetime) as clock:
                    clock.now.return_value = NOW
                    with mock.patch("sys.stdout"):
                        self.assertEqual(module.main(), 0)

if __name__ == "__main__":
    unittest.main()
