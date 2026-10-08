"""Fail-closed tests for offline serialized owner handoff."""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "zcloud_owner_handoff_check.py"
SPEC = importlib.util.spec_from_file_location("handoff", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def valid():
    record = {key: "evidence" for key in module.FIELDS}
    record.update(repository="Zennay/zCloud", gate="#1089",
                  operation_class="deploy", outgoing_owner="worker-a",
                  incoming_owner="worker-b", main_sha="a" * 40,
                  candidate_head_sha="b" * 40)
    record.update({key: False for key in module.FLAGS})
    record.update({key: True for key in module.CHECKS})
    return record


class HandoffTests(unittest.TestCase):
    def test_valid_record_is_only_consistent_not_authorized(self):
        result = module.evaluate(valid())
        self.assertTrue(result["handoff_consistent"])
        self.assertFalse(any(result[key] for key in module.FLAGS))

    def test_missing_explicit_acceptance_fails_closed(self):
        record = valid()
        record["incoming_explicit_acceptance"] = False
        self.assertFalse(module.evaluate(record)["handoff_consistent"])

    def test_stale_or_malformed_sha_fails_closed(self):
        record = valid()
        record["main_sha"] = "unknown"
        self.assertIn("invalid_main_sha", module.evaluate(record)["reasons"])

    def test_owner_collision_fails_closed(self):
        record = valid()
        record["incoming_owner"] = record["outgoing_owner"]
        self.assertIn("owners_not_distinct", module.evaluate(record)["reasons"])

    def test_authorization_true_rejected_even_after_acceptance(self):
        for flag in module.FLAGS:
            record = valid()
            record[flag] = True
            result = module.evaluate(record)
            self.assertFalse(result["handoff_consistent"])
            self.assertFalse(result[flag])

    def test_malformed_record_fails_closed(self):
        for record in (None, [], {}, "text"):
            result = module.evaluate(record)
            self.assertFalse(result["handoff_consistent"])
            self.assertFalse(result["deploy_authorized"])

    def test_missing_rollback_owner_fails_closed(self):
        record = valid()
        record["rollback_owner"] = ""
        self.assertIn("missing_rollback_owner", module.evaluate(record)["reasons"])


if __name__ == "__main__":
    unittest.main()
