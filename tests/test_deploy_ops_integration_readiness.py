import unittest

from scripts.zcloud_deploy_ops_integration_readiness import (
    classify,
    inventory,
    is_deploy_ops_pr,
    latest_exact_head_regression,
    render_markdown,
)


SHA_A = "a" * 40
SHA_B = "b" * 40


def pr(number=1, title="Deploy-ops safety", body="", head="worker/deploy-ops-test", sha=SHA_A):
    return {
        "number": number,
        "title": title,
        "body": body,
        "head": {"ref": head, "sha": sha},
    }


def regression(sha=SHA_A, status="completed", conclusion="success", run_id=10, created="2026-10-06T10:00:00Z"):
    return {
        "id": run_id,
        "name": "zCloud regression smoke",
        "head_sha": sha,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
    }


class DeployOpsIntegrationReadinessTests(unittest.TestCase):
    def test_selector_requires_explicit_deploy_ops_marker_or_branch(self):
        self.assertTrue(is_deploy_ops_pr(pr()))
        self.assertTrue(
            is_deploy_ops_pr(
                pr(
                    title="Bound canary timeout",
                    body="Independent deploy-ops hardening.",
                    head="feature/canary-timeout",
                )
            )
        )
        self.assertTrue(
            is_deploy_ops_pr(
                pr(
                    title="Bound canary timeout",
                    body="",
                    head="fix/deploy-readiness-budget",
                )
            )
        )
        self.assertFalse(
            is_deploy_ops_pr(
                pr(
                    title="Control plane activity",
                    body="Read-only observability.",
                    head="feature/control-plane-activity",
                )
            )
        )
        self.assertFalse(
            is_deploy_ops_pr(
                pr(
                    title="Control plane activity",
                    body="Control-plane owner. " + ("x" * 600) + " Does not overlap deploy-ops.",
                    head="feature/control-plane-activity",
                )
            )
        )

    def test_behind_main_always_requires_restack_even_when_green(self):
        row = classify(
            pr(),
            {"behind_by": 3, "ahead_by": 2},
            [regression()],
        )
        self.assertEqual("restack_required", row.decision)
        self.assertEqual("success", row.regression)
        self.assertIn("behind_main:3", row.reasons)
        self.assertIn("green_regression_is_pre_restack", row.reasons)

    def test_current_green_exact_head_is_ready(self):
        row = classify(
            pr(),
            {"behind_by": 0, "ahead_by": 2},
            [regression()],
        )
        self.assertEqual("ready", row.decision)
        self.assertEqual((), row.reasons)

    def test_missing_regression_requires_revalidation(self):
        row = classify(pr(), {"behind_by": 0, "ahead_by": 1}, [])
        self.assertEqual("revalidation_required", row.decision)
        self.assertEqual("missing", row.regression)

    def test_in_progress_regression_is_not_ready(self):
        row = classify(
            pr(),
            {"behind_by": 0, "ahead_by": 1},
            [regression(status="in_progress", conclusion=None)],
        )
        self.assertEqual("revalidation_in_progress", row.decision)

    def test_failed_regression_is_not_ready(self):
        row = classify(
            pr(),
            {"behind_by": 0, "ahead_by": 1},
            [regression(conclusion="failure")],
        )
        self.assertEqual("revalidation_failed", row.decision)
        self.assertIn("exact_head_regression_failure", row.reasons)

    def test_latest_exact_head_regression_ignores_old_head_and_old_run(self):
        runs = [
            regression(sha=SHA_B, run_id=99, created="2026-10-06T12:00:00Z"),
            regression(run_id=10, conclusion="failure", created="2026-10-06T10:00:00Z"),
            regression(run_id=11, conclusion="success", created="2026-10-06T11:00:00Z"),
        ]
        state, run = latest_exact_head_regression(runs, SHA_A)
        self.assertEqual("success", state)
        self.assertEqual(11, run["id"])

    def test_invalid_evidence_fails_closed(self):
        bad = pr(sha="not-a-sha")
        row = classify(bad, None, [])
        self.assertEqual("evidence_unavailable", row.decision)
        self.assertIn("invalid_head_sha", row.reasons)

    def test_inventory_is_bounded_to_deploy_ops_and_sorted(self):
        class FakeReader:
            def open_pulls(self):
                return [
                    pr(number=9, title="Deploy-ops B", sha=SHA_B),
                    pr(
                        number=3,
                        title="Control plane",
                        body="not deploy work",
                        head="feature/control-plane",
                        sha="c" * 40,
                    ),
                    pr(number=4, title="Deploy-ops A", sha=SHA_A),
                ]

            def compare(self, base, head_sha):
                self.assert_base = base
                return {"behind_by": 0, "ahead_by": 1}

            def workflow_runs(self, head_sha):
                return [regression(sha=head_sha)]

        reader = FakeReader()
        rows = inventory(reader, "main")
        self.assertEqual([4, 9], [row.number for row in rows])
        self.assertTrue(all(row.decision == "ready" for row in rows))

    def test_markdown_exposes_only_bounded_readiness_fields(self):
        row = classify(pr(title="Safe readiness"), {"behind_by": 0, "ahead_by": 1}, [regression()])
        output = render_markdown([row], "Zennay/zCloud", "main")
        self.assertIn("#1", output)
        self.assertIn("ready", output)
        self.assertIn(SHA_A[:12], output)
        self.assertIn("ready never authorizes merge or deploy", output)
        self.assertNotIn(SHA_A, output)


if __name__ == "__main__":
    unittest.main()
