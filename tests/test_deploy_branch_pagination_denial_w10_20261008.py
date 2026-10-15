"""Offline fail-closed contract for paginated PR-less branch ownership.

Specification fixture only: no GitHub API, CI runner, production gate or deploy.
The eventual ownership preflight must implement and independently verify this
contract before these checks can serve as integration evidence.
"""
import unittest


def inventory_pages(pages, *, expected_main, max_pages=10, max_branches=500):
    """Return a denial-only inventory verdict from bounded API page fixtures.

    Each page contains: main_sha, cursor, next_cursor, branches, and complete.
    'complete' means this response is authoritative, not that more pages exist.
    Every branch is {name, changed_paths, covered_by_pr}.
    """
    if not isinstance(expected_main, str) or len(expected_main) != 40:
        return {"clear": False, "reason": "invalid_snapshot"}
    if not isinstance(pages, list) or not pages:
        return {"clear": False, "reason": "missing_pages"}
    seen_cursors = {None}
    expected_cursor = None
    total = 0
    found_overlap = False
    for index, page in enumerate(pages):
        if index >= max_pages:
            return {"clear": False, "reason": "page_cap"}
        if not isinstance(page, dict) or page.get("complete") is not True:
            return {"clear": False, "reason": "partial_response"}
        if page.get("main_sha") != expected_main:
            return {"clear": False, "reason": "main_drift"}
        if page.get("cursor") != expected_cursor:
            return {"clear": False, "reason": "cursor_mismatch"}
        branches = page.get("branches")
        if not isinstance(branches, list):
            return {"clear": False, "reason": "invalid_branches"}
        total += len(branches)
        if total > max_branches:
            return {"clear": False, "reason": "branch_cap"}
        for branch in branches:
            if not isinstance(branch, dict) or not isinstance(branch.get("changed_paths"), list):
                return {"clear": False, "reason": "invalid_branch"}
            if branch.get("covered_by_pr") is not True and any(
                path.startswith(".github/workflows/") for path in branch["changed_paths"]
            ):
                found_overlap = True
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            if index != len(pages) - 1:
                return {"clear": False, "reason": "trailing_pages"}
            return {"clear": not found_overlap, "reason": "overlap" if found_overlap else "no_overlap"}
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            return {"clear": False, "reason": "duplicate_cursor"}
        seen_cursors.add(next_cursor)
        expected_cursor = next_cursor
    return {"clear": False, "reason": "incomplete_pagination"}


SHA = "a" * 40


def page(cursor, next_cursor, branches=(), sha=SHA, complete=True):
    return dict(cursor=cursor, next_cursor=next_cursor,
                branches=list(branches), main_sha=sha, complete=complete)


class BranchPaginationDenialContract(unittest.TestCase):
    def assert_denied(self, pages):
        self.assertFalse(inventory_pages(pages, expected_main=SHA)["clear"])

    def test_last_page_overlap_denied(self):
        self.assert_denied([
            page(None, "p2"),
            page("p2", None, [{"name": "unclaimed", "covered_by_pr": False,
                                "changed_paths": [".github/workflows/deploy.yml"]}]),
        ])

    def test_missing_final_page_denied(self):
        self.assert_denied([page(None, "p2")])

    def test_repeated_cursor_denied(self):
        self.assert_denied([page(None, "p2"), page("p2", "p2")])

    def test_moving_main_denied(self):
        self.assert_denied([page(None, "p2"), page("p2", None, sha="b" * 40)])

    def test_api_partial_response_denied(self):
        self.assert_denied([page(None, "p2"), page("p2", None, complete=False)])

    def test_page_cap_denied(self):
        pages = [page(None, "p2"), page("p2", None)]
        self.assertFalse(inventory_pages(pages, expected_main=SHA, max_pages=1)["clear"])

    def test_branch_cap_denied(self):
        self.assertFalse(inventory_pages([page(None, None, [{ "name": "a", "changed_paths": [] }])],
                                         expected_main=SHA, max_branches=0)["clear"])

    def test_deduplicated_pr_head_not_claimed_as_prless_overlap(self):
        result = inventory_pages([page(None, None, [
            {"name": "has-pr", "covered_by_pr": True, "changed_paths": [".github/workflows/deploy.yml"]}
        ])], expected_main=SHA)
        self.assertTrue(result["clear"])

    def test_empty_last_page_valid(self):
        self.assertTrue(inventory_pages([page(None, "p2"), page("p2", None)],
                                        expected_main=SHA)["clear"])

    def test_unexpected_additional_page_denied(self):
        self.assert_denied([page(None, None), page(None, None)])


if __name__ == "__main__":
    unittest.main()
