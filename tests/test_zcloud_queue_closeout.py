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

    def test_successful_main_workflow_with_real_artifact_can_close_noncode_work(self):
        url = "https://github.com/Zennay/Ftmo/actions/runs/12345"

        def run_data(path):
            if path == "/repos/Zennay/Ftmo/actions/runs/12345":
                return {
                    "status": "completed", "conclusion": "success", "head_branch": "main",
                    "event": "schedule", "created_at": "2026-10-10T13:20:00Z",
                }
            if path == "/repos/Zennay/Ftmo/actions/runs/12345/artifacts?per_page=100":
                return {"total_count": 1, "artifacts": [
                    {"name": "validated-experiment", "size_in_bytes": 2048, "expired": False}
                ]}
            raise AssertionError("Unexpected GitHub endpoint " + path)

        self.assertTrue(verified_merged_delivery(
            url, run_data, expected_repo="Ftmo", claimed_after="2026-10-10T13:00:00Z",
        )[0])
        self.assertFalse(verified_merged_delivery(
            url, run_data, expected_repo="zCloud", claimed_after="2026-10-10T13:00:00Z",
        )[0])
        self.assertFalse(verified_merged_delivery(
            url, run_data, expected_repo="Ftmo", claimed_after="2026-10-10T14:00:00Z",
        )[0])

        for update in (
            {"status": "in_progress"},
            {"conclusion": "failure"},
            {"head_branch": "feature"},
            {"event": "pull_request"},
        ):
            def invalid_run(path):
                data = run_data(path)
                return {**data, **update} if path.endswith("/runs/12345") else data
            self.assertFalse(verified_merged_delivery(url, invalid_run)[0])

        for artifacts in (
            [],
            [{"size_in_bytes": 0, "expired": False}],
            [{"size_in_bytes": 20, "expired": True}],
        ):
            def invalid_artifact(path):
                data = run_data(path)
                return {"artifacts": artifacts} if "/artifacts?" in path else data
            self.assertFalse(verified_merged_delivery(url, invalid_artifact)[0])

        def incomplete(path):
            data = run_data(path)
            return {**data, "total_count": 101} if "/artifacts?" in path else data
        self.assertFalse(verified_merged_delivery(url, incomplete)[0])

    def test_denies_github_errors_and_oversized_evidence(self):
        def down(_):
            raise OSError("Network unavailable")
        self.assertFalse(verified_merged_delivery(PR_URL, down)[0])
        self.assertFalse(verified_merged_delivery(PR_URL + " " * 4100, github_fixture)[0])


if __name__ == "__main__":
    unittest.main()
