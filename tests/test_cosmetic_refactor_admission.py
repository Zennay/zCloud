import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_cosmetic_refactor_admission as policy


def payload(kind="cosmetic_refactor", issues=None):
    return {
        "schema_version": 1,
        "change": {"id": "change-1", "kind": kind},
        "open_reliability_issues": list(issues or []),
    }


class CosmeticRefactorAdmissionTests(unittest.TestCase):
    def test_cosmetic_refactor_is_blocked_by_open_p0_or_p1(self):
        result = policy.decide(
            payload(
                issues=[
                    {"id": "rel-p2", "priority": "P2"},
                    {"id": "rel-p1", "priority": "P1"},
                    {"id": "rel-p0", "priority": "P0"},
                ]
            )
        )
        self.assertEqual("RELIABILITY_WORK_REQUIRED", result["decision"])
        self.assertEqual(
            ["rel-p0", "rel-p1"],
            result["urgent_reliability_ids"],
        )
        self.assertEqual(
            "cosmetic_refactor_blocked_by_open_p0_p1_reliability",
            result["reason_code"],
        )

    def test_cosmetic_refactor_is_allowed_when_only_lower_priority_reliability_remains(self):
        result = policy.decide(
            payload(issues=[{"id": "rel-p2", "priority": "P2"}])
        )
        self.assertEqual("ALLOWED", result["decision"])
        self.assertEqual(0, result["urgent_reliability_count"])

    def test_non_cosmetic_change_is_not_blocked_by_this_policy(self):
        for kind in (
            "functional",
            "reliability_fix",
            "security_fix",
            "docs_only",
            "test_only",
        ):
            with self.subTest(kind=kind):
                result = policy.decide(
                    payload(kind=kind, issues=[{"id": "rel-p0", "priority": "P0"}])
                )
                self.assertEqual("ALLOWED", result["decision"])

    def test_urgent_issue_order_is_stable(self):
        result = policy.decide(
            payload(
                issues=[
                    {"id": "z", "priority": "P1"},
                    {"id": "b", "priority": "P0"},
                    {"id": "a", "priority": "P0"},
                ]
            )
        )
        self.assertEqual(["a", "b", "z"], result["urgent_reliability_ids"])

    def test_duplicate_or_noncanonical_issue_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            policy.decide(
                payload(
                    issues=[
                        {"id": "dup", "priority": "P1"},
                        {"id": "dup", "priority": "P0"},
                    ]
                )
            )
        with self.assertRaises(ValueError):
            policy.decide(payload(issues=[{"id": "../bad", "priority": "P1"}]))

    def test_unknown_or_hidden_fields_fail_closed_without_being_ignored(self):
        value = payload()
        value["change"]["patch"] = "hidden"
        with self.assertRaises(ValueError):
            policy.decide(value)

        value = payload(issues=[{"id": "rel", "priority": "P1"}])
        value["open_reliability_issues"][0]["title"] = "raw issue title"
        with self.assertRaises(ValueError):
            policy.decide(value)

    def test_strict_schema_and_priorities(self):
        for version in (True, 1.0, "1", 2):
            value = payload()
            value["schema_version"] = version
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    policy.decide(value)
        with self.assertRaises(ValueError):
            policy.decide(payload(issues=[{"id": "rel", "priority": "critical"}]))
        value = payload()
        value["change"]["kind"] = "refactor"
        with self.assertRaises(ValueError):
            policy.decide(value)

    def test_issue_count_is_bounded(self):
        issues = [
            {"id": f"rel-{index}", "priority": "P2"}
            for index in range(policy.MAX_ISSUES + 1)
        ]
        with self.assertRaises(ValueError):
            policy.decide(payload(issues=issues))

    def test_file_input_is_bounded_and_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="zcloud-cosmetic-policy-") as tmp:
            root = Path(tmp)
            good = root / "good.json"
            good.write_bytes(b"{}")
            self.assertEqual(b"{}", policy._load_bytes(good))

            large = root / "large.json"
            large.write_bytes(b"x" * (policy.MAX_INPUT_BYTES + 1))
            with self.assertRaises(ValueError):
                policy._load_bytes(large)

            link = root / "link.json"
            link.symlink_to(good)
            with self.assertRaises(ValueError):
                policy._load_bytes(link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
