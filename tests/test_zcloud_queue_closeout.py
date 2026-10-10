"""Off-network verification contract for DONE queue finalization."""
import unittest

from scripts.zcloud_queue_closeout import verified_merged_delivery

PR_URL = "https://github.com/Zennay/zCloud/pull/646"
SHA = "a" * 40


def github_fixture(path):
    if path == "/repos/Zennay/zCloud/pulls/646":
        return {"merged_at": "2026-10-10T12:50:00Z", "base": {"ref": "main"}, "merge_commit_sha": SHA}
    if path == "/repos/Zennay/zCloud/commits/" + SHA + "/check-runs?per_page=100":
        return {"check_runs": [{"status": "completed", "conclusion": "success"}]}
    raise AssertionError("Unexpected GitHub path " + path)


class QueueCloseoutTests(unittest.TestCase):
    def test_merged_exact_sha_green(self):
        self.assertEqual(
            (True, "Merged PR and exact-merge-SHA checks verified"),
            verified_merged_delivery(PR_URL + " implemented and tested", github_fixture),
        )

    def test_refuses_issue_creation_status_or_unmerged_pr(self):
        for evidence in ("", "added issue #1276", "opened PR #646", "commit abc; tests green",
                         "https://github.com/Zennay/zCloud/issues/1278"):
            with self.subTest(evidence=evidence):
                allowed, _ = verified_merged_delivery(evidence, github_fixture)
                self.assertFalse(allowed)

        def unmerged(path):
            result = github_fixture(path)
            return {**result, "merged_at": None} if "/pulls/" in path else result
        self.assertFalse(verified_merged_delivery(PR_URL, unmerged)[0])

    def test_denies_untrusted_repo_other_branch_and_wrong_sha(self):
        self.assertFalse(verified_merged_delivery("https://github.com/evil/zCloud/pull/646", github_fixture)[0])
        self.assertFalse(verified_merged_delivery(PR_URL + " " + PR_URL, github_fixture)[0])

        def other_branch(path):
            obj = github_fixture(path)
            return {**obj, "base": {"ref": "develop"}} if "/pulls/" in path else obj

        def wrong_sha(path):
            obj = github_fixture(path)
            return {**obj, "merge_commit_sha": "bad"} if "/pulls/" in path else obj

        self.assertFalse(verified_merged_delivery(PR_URL, other_branch)[0])
        self.assertFalse(verified_merged_delivery(PR_URL, wrong_sha)[0])

    def test_requires_repository_and_current_claim_window(self):
        self.assertFalse(
            verified_merged_delivery(PR_URL, github_fixture, expected_repo="Supa")[0]
        )
        self.assertTrue(
            verified_merged_delivery(
                PR_URL, github_fixture, expected_repo="zcloud",
                claimed_after="2026-10-10T12:40:00Z",
            )[0]
        )
        self.assertFalse(
            verified_merged_delivery(
                PR_URL, github_fixture, expected_repo="zCloud",
                claimed_after="2026-10-10T13:40:00Z",
            )[0]
        )
        self.assertFalse(
            verified_merged_delivery(
                PR_URL, github_fixture, expected_repo="zCloud",
                claimed_after="malformed-clock",
            )[0]
        )

    def test_denies_pending_failed_or_missing_checks(self):
        for conclusion, status in (("failure", "completed"), ("success", "in_progress"), ("skipped", "completed")):
            with self.subTest(conclusion=conclusion, status=status):
                def checks(path):
                    obj = github_fixture(path)
                    return {"check_runs": [{"status": status, "conclusion": conclusion}]} if "/check-runs?" in path else obj
                self.assertFalse(verified_merged_delivery(PR_URL, checks)[0])
        def none(path):
            obj = github_fixture(path)
            return {"check_runs": []} if "/check-runs?" in path else obj
        self.assertFalse(verified_merged_delivery(PR_URL, none)[0])
        def incomplete(path):
            obj = github_fixture(path)
            return {"total_count": 101, "check_runs": obj["check_runs"]} if "/check-runs?" in path else obj
        self.assertFalse(verified_merged_delivery(PR_URL, incomplete)[0])

    def test_denies_github_errors_and_oversized_evidence(self):
        def down(_):
            raise OSError("Network unavailable")
        self.assertFalse(verified_merged_delivery(PR_URL, down)[0])
        self.assertFalse(verified_merged_delivery(PR_URL + " " * 4100, github_fixture)[0])


if __name__ == "__main__":
    unittest.main()
