"""Fail-closed pagination tests for the #870 recent un-PR branch inventory.

All cases use an injected pure GitHub response runner. No network, repository,
SQLite, VPS, or worker control operations are performed.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import zcloud_recent_branch_snapshot as collector
import zcloud_recent_branch_ownership_audit as audit


SHA = "a" * 40
STAMP = "2026-10-13T07:00:00Z"


def node(name):
    return {"name": name, "target": {"oid": SHA, "committedDate": STAMP}}


def page(nodes, *, next_page=False, cursor=None):
    return {
        "data": {
            "repository": {
                "refs": {
                    "nodes": nodes,
                    "pageInfo": {
                        "hasNextPage": next_page,
                        "endCursor": cursor,
                    },
                }
            }
        }
    }


class BranchPaginationFailClosedTests(unittest.TestCase):
    def test_repeated_cursor_with_distinct_nodes_is_rejected(self):
        responses = [
            page([node("main")], next_page=True, cursor="same"),
            page([node("work")], next_page=True, cursor="same"),
        ]
        calls = []

        def runner(args):
            self.assertEqual(args[:3], ["gh", "api", "graphql"])
            calls.append(args)
            return responses.pop(0)

        with self.assertRaisesRegex(collector.CollectorError, "branch_cursor_repeated"):
            collector._branch_refs("Zennay", "zCloud", runner)
        self.assertEqual(len(calls), 2)

    def test_empty_nonterminal_page_is_rejected_without_retry(self):
        calls = []

        def runner(args):
            calls.append(args)
            return page([], next_page=True, cursor="advance")

        with self.assertRaisesRegex(collector.CollectorError, "branch_page_empty"):
            collector._branch_refs("Zennay", "zCloud", runner)
        self.assertEqual(len(calls), 1)

    def test_partial_graphql_data_with_errors_is_rejected(self):
        response = page([node("main")], next_page=False)
        response["errors"] = [{"message": "partial repository visibility"}]
        calls = []

        def runner(args):
            calls.append(args)
            return response

        with self.assertRaisesRegex(collector.CollectorError, "branch_graphql_errors"):
            collector._branch_refs("Zennay", "zCloud", runner)
        self.assertEqual(len(calls), 1)

    def test_malformed_graphql_error_envelope_is_rejected(self):
        for invalid in (None, {}, "error", True):
            with self.subTest(invalid=invalid):
                response = page([node("main")], next_page=False)
                response["errors"] = invalid
                with self.assertRaisesRegex(collector.CollectorError, "branch_graphql_errors"):
                    collector._branch_refs("Zennay", "zCloud", lambda args: response)

    def test_explicit_empty_graphql_errors_does_not_mask_valid_page(self):
        response = page([node("main")], next_page=False)
        response["errors"] = []
        rows = collector._branch_refs("Zennay", "zCloud", lambda args: response)
        self.assertEqual([r["name"] for r in rows], ["main"])

    def test_zero_node_last_page_is_allowed(self):
        responses = [
            page([node("main")], next_page=True, cursor="one"),
            page([], next_page=False),
        ]
        items = collector._branch_refs("Zennay", "zCloud", lambda args: responses.pop(0))
        self.assertEqual(tuple(row["name"] for row in items), ("main",))

    def test_page_cap_blocks_unbounded_cursor_progression(self):
        counter = 0

        def runner(args):
            nonlocal counter
            counter += 1
            return page([node(f"branch-{counter}")], next_page=True, cursor=f"cursor-{counter}")

        with patch.object(collector, "MAX_BRANCH_PAGES", 2):
            with self.assertRaisesRegex(collector.CollectorError, "branch_page_bound_exceeded"):
                collector._branch_refs("Zennay", "zCloud", runner)
        self.assertEqual(counter, 2)

    def test_normal_two_page_scan_preserves_exact_ref_metadata(self):
        responses = [
            page([node("main")], next_page=True, cursor="one"),
            page([node("worker/test")], next_page=False),
        ]
        calls = []

        def runner(args):
            calls.append(args)
            return responses.pop(0)

        items = collector._branch_refs("Zennay", "zCloud", runner)
        self.assertEqual([item["name"] for item in items], ["main", "worker/test"])
        self.assertTrue(all(item["sha"] == SHA and item["committed_at"] == STAMP for item in items))
        self.assertEqual(len(calls), 2)
        self.assertIn("endCursor=one", calls[1])


    def _compare_fixture_runner(self, file_field, compare_calls=None):
        def runner(args):
            joined = " ".join(args)
            if "/git/ref/heads/main" in joined:
                return {"object": {"sha": SHA}}
            if args[:3] == ["gh", "pr", "list"]:
                return []
            if args[:3] == ["gh", "api", "graphql"]:
                return page(
                    [node("main"), node("recent-unpr")],
                    next_page=False,
                )
            if "/compare/" in joined:
                if compare_calls is not None:
                    compare_calls.append(args)
                result = {
                    "ahead_by": 1,
                    "behind_by": 0,
                    "merge_base_commit": {"sha": SHA},
                }
                if file_field is not None:
                    result["files"] = file_field
                return result
            raise AssertionError(args)

        return runner

    def test_missing_compare_file_evidence_is_not_an_empty_diff(self):
        when = collector.parse_utc("2026-10-13T07:05:00Z")
        with self.assertRaisesRegex(collector.CollectorError, "compare_payload_invalid"):
            collector.collect_snapshot(
                "Zennay/zCloud",
                when,
                72,
                self._compare_fixture_runner(None),
            )

    def test_jq_preserves_missing_file_array_as_null(self):
        # Without this expression, .files[]? converts missing API evidence
        # into an apparently complete empty list before Python sees it.
        calls = []
        when = collector.parse_utc("2026-10-13T07:05:00Z")
        collector.collect_snapshot(
            "Zennay/zCloud", when, 72,
            self._compare_fixture_runner([], compare_calls=calls),
        )
        self.assertEqual(len(calls), 1)
        projected = " ".join(calls[0])
        self.assertIn('(.files|type)==', projected)
        self.assertIn('else null end', projected)
        self.assertNotIn('.files[]?', projected)

    def test_explicit_empty_compare_file_list_is_supported(self):
        when = collector.parse_utc("2026-10-13T07:05:00Z")
        snapshot = collector.collect_snapshot(
            "Zennay/zCloud", when, 72, self._compare_fixture_runner([])
        )
        target = next(b for b in snapshot["branches"] if b["name"] == "recent-unpr")
        self.assertEqual(target["compare"]["files"], [])
        self.assertTrue(target["compare"]["file_list_complete"])



    @staticmethod
    def _snapshot_count_fixture():
        return {
            "schema_version": collector.SCHEMA_VERSION,
            "repository": "Zennay/zCloud",
            "main_sha": SHA,
            "as_of": STAMP,
            "recent_hours": 72,
            "branch_count": 1,
            "open_pr_head_count": 0,
            "branches": [
                {
                    "name": "main",
                    "sha": SHA,
                    "committed_at": STAMP,
                    "has_open_pr": False,
                    "compare": None,
                }
            ],
        }

    def test_strict_branch_cardinality_rejects_boolean_and_float(self):
        for invalid in (True, 1.0, "1"):
            with self.subTest(invalid=invalid):
                sample = self._snapshot_count_fixture()
                sample["branch_count"] = invalid
                with self.assertRaisesRegex(audit.AuditError, "snapshot_branch_count_mismatch"):
                    audit.parse_snapshot(sample)

    def test_missing_or_boolean_open_pr_count_is_denied(self):
        for invalid in (None, False, 0.0, "0"):
            with self.subTest(invalid=invalid):
                sample = self._snapshot_count_fixture()
                if invalid is None:
                    del sample["open_pr_head_count"]
                else:
                    sample["open_pr_head_count"] = invalid
                with self.assertRaisesRegex(audit.AuditError, "snapshot_open_pr_head_count_invalid"):
                    audit.parse_snapshot(sample)

    def test_open_pr_count_mismatch_is_denied(self):
        sample = self._snapshot_count_fixture()
        sample["open_pr_head_count"] = 1
        with self.assertRaisesRegex(audit.AuditError, "snapshot_open_pr_head_count_mismatch"):
            audit.parse_snapshot(sample)

    def test_coherent_snapshot_counts_still_pass(self):
        sample = self._snapshot_count_fixture()
        self.assertEqual(len(audit.parse_snapshot(sample)["branches"]), 1)



if __name__ == "__main__":
    unittest.main()
