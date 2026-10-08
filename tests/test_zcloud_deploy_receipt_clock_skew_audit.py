import datetime as dt
import importlib.util
import pathlib
import unittest

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

    def test_policy_bounds(self):
        self.assertEqual(self.check({"observed_at": "2026-10-08T01:00:00Z"}, future_skew_seconds=999)["reason"], "INVALID_RECEIPT")
        self.assertEqual(self.check({"observed_at": "2026-10-08T01:00:00Z"}, max_age_seconds=-1)["reason"], "INVALID_RECEIPT")

if __name__ == "__main__":
    unittest.main()
