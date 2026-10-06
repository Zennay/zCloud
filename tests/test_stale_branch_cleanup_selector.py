import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_stale_branch_cleanup_selector as selector


NOW = datetime(2026, 10, 6, 21, 50, tzinfo=timezone.utc)
SHA = "a" * 40


def record(**overrides):
    value = {
        "name": "diagnostic/old-proof-20260801",
        "sha": SHA,
        "protected": False,
        "default_branch": False,
        "open_prs": 0,
        "active_claim": False,
        "active_issue": False,
        "merged_to_default": True,
        "last_commit_at": "2026-08-01T12:00:00Z",
    }
    value.update(overrides)
    return value


class StaleBranchSelectorTests(unittest.TestCase):
    def test_only_old_merged_unclaimed_safe_namespace_is_candidate(self):
        result = selector.select([record()], now=NOW)
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(0, result["blocked_count"])
        self.assertFalse(result["mutation_performed"])

    def test_blocks_every_high_risk_state(self):
        cases = [
            ("default_branch", {"default_branch": True}, "DEFAULT_BRANCH"),
            ("protected", {"protected": True}, "PROTECTED"),
            ("open_pr", {"open_prs": 1}, "OPEN_PR"),
            ("active_claim", {"active_claim": True}, "ACTIVE_CLAIM"),
            ("active_issue", {"active_issue": True}, "ACTIVE_ISSUE"),
            ("unmerged", {"merged_to_default": False}, "NOT_MERGED_TO_DEFAULT"),
            ("recent", {"last_commit_at": "2026-10-01T00:00:00Z"}, "TOO_RECENT"),
            ("namespace", {"name": "feature/important-work"}, "UNSAFE_NAMESPACE"),
        ]
        for label, overrides, expected in cases:
            with self.subTest(label=label):
                result = selector.select([record(**overrides)], now=NOW)
                self.assertEqual(0, result["candidate_count"])
                self.assertIn(expected, result["blocked"][0]["reasons"])

    def test_rejects_loose_types_and_unknown_mutation_fields(self):
        with self.assertRaises(selector.InputError):
            selector.select([record(open_prs=False)], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([{**record(), "delete": True}], now=NOW)

    def test_rejects_future_timestamp(self):
        with self.assertRaises(selector.InputError):
            selector.select([record(last_commit_at="2026-10-07T00:00:00Z")], now=NOW)

    def test_bounds_record_count(self):
        with self.assertRaises(selector.InputError):
            selector.select([record(name=f"tmp/x-{i}") for i in range(selector.MAX_BRANCHES + 1)], now=NOW)

    def test_load_payload_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "payload.json"
            target.write_text(json.dumps([record()]), encoding="utf-8")
            link = root / "link.json"
            os.symlink(target, link)
            with self.assertRaises(selector.InputError):
                selector.load_payload(link)

    def test_sorting_is_deterministic(self):
        result = selector.select(
            [
                record(name="debug/b", last_commit_at="2026-07-01T00:00:00Z"),
                record(name="debug/a", last_commit_at="2026-07-01T00:00:00Z"),
                record(name="audit/older", last_commit_at="2026-06-01T00:00:00Z"),
            ],
            now=NOW,
        )
        self.assertEqual(
            ["audit/older", "debug/a", "debug/b"],
            [item["name"] for item in result["candidates"]],
        )


if __name__ == "__main__":
    unittest.main()
