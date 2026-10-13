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

        with patch.object(collector, "MAX_BRANCHES", 2):
            with self.assertRaisesRegex(collector.CollectorError, "branch_page_bound_exceeded"):
                collector._branch_refs("Zennay", "zCloud", runner)
        self.assertEqual(counter, 3)

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


if __name__ == "__main__":
    unittest.main()
