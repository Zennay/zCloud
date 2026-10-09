import sqlite3
import unittest

import completion_controller as cc


def _conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    cc.init_tables(connection)
    return connection


class ClassifyPrTests(unittest.TestCase):
    NOW = "2026-10-09T20:00:00+00:00"

    def test_clean_green_pr_is_mergeable_and_auto_merge_eligible(self):
        pr = {
            "draft": False,
            "mergeable_state": "clean",
            "updated_at": "2026-10-09T19:00:00Z",
            "body": "Adds the missing test.",
            "title": "Fix flaky test",
        }
        checks = [{"status": "completed", "conclusion": "success"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        self.assertEqual(result["label"], "mergeable")
        self.assertTrue(result["auto_merge_eligible"])

    def test_dirty_mergeable_state_is_conflicting_and_never_auto_merged(self):
        pr = {"draft": False, "mergeable_state": "dirty", "updated_at": "2026-10-09T19:00:00Z"}
        checks = [{"status": "completed", "conclusion": "success"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        self.assertEqual(result["label"], "conflicting")
        self.assertFalse(result["auto_merge_eligible"])

    def test_failing_check_blocks_auto_merge(self):
        pr = {"draft": False, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z"}
        checks = [{"status": "completed", "conclusion": "failure"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        self.assertEqual(result["label"], "ci_failing")
        self.assertFalse(result["auto_merge_eligible"])

    def test_pending_check_is_treated_as_ci_failing_not_silently_green(self):
        pr = {"draft": False, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z"}
        checks = [{"status": "in_progress", "conclusion": None}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        self.assertEqual(result["label"], "ci_failing")
        self.assertFalse(result["auto_merge_eligible"])

    def test_draft_pr_is_draft_in_progress(self):
        pr = {"draft": True, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z"}
        checks = [{"status": "completed", "conclusion": "success"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        self.assertEqual(result["label"], "draft_in_progress")
        self.assertFalse(result["auto_merge_eligible"])

    def test_changes_requested_review_blocks_auto_merge(self):
        pr = {
            "draft": False, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z",
            "review_decision": "CHANGES_REQUESTED",
        }
        checks = [{"status": "completed", "conclusion": "success"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        self.assertEqual(result["label"], "needs_review")
        self.assertFalse(result["auto_merge_eligible"])

    def test_prose_do_not_merge_marker_vetoes_auto_merge_even_if_clean(self):
        pr = {
            "draft": False, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z",
            "body": "Keep draft until fresh regression and self-hosted OOM/control-plane validation are terminal green.",
        }
        checks = [{"status": "completed", "conclusion": "success"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW)
        # Explicitly a real body excerpt from zCloud PR #580: draft=False in
        # the API but the author's own prose says not to merge yet.
        self.assertFalse(result["auto_merge_eligible"])
        self.assertTrue(result["do_not_merge_marker"])

    def test_old_pr_is_reclassified_stale_even_with_green_checks(self):
        pr = {"draft": False, "mergeable_state": "clean", "updated_at": "2026-09-01T00:00:00Z"}
        checks = [{"status": "completed", "conclusion": "success"}]
        result = cc.classify_pr(pr, checks, now_value=self.NOW, stale_after_seconds=7 * 24 * 3600)
        self.assertEqual(result["label"], "stale")
        self.assertFalse(result["auto_merge_eligible"])

    def test_conflicting_takes_priority_over_staleness_label(self):
        pr = {"draft": False, "mergeable_state": "dirty", "updated_at": "2026-09-01T00:00:00Z"}
        result = cc.classify_pr(pr, [], now_value=self.NOW)
        self.assertEqual(result["label"], "conflicting")

    def test_no_checks_at_all_is_not_auto_merge_eligible(self):
        # A PR with zero reported check runs should never be treated as
        # "all signals green" by omission.
        pr = {"draft": False, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z"}
        result = cc.classify_pr(pr, [], now_value=self.NOW)
        self.assertFalse(result["auto_merge_eligible"])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()

    def test_record_and_fetch_pr_state_roundtrip(self):
        pr = {"title": "Fix things", "html_url": "https://github.com/x/y/pull/1", "mergeable_state": "dirty", "draft": False}
        classification = cc.classify_pr(pr, [])
        cc.record_pr_state(self.conn, "cloud", "zCloud", 580, pr, classification)
        rows = self.conn.execute("SELECT * FROM pr_state").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "conflicting")

    def test_open_unfinished_work_excludes_mergeable_and_orders_by_severity(self):
        base = {"draft": False, "updated_at": "2026-10-09T19:00:00Z"}
        for number, state, checks in [
            (1, "clean", [{"status": "completed", "conclusion": "success"}]),  # mergeable
            (2, "dirty", []),  # conflicting
            (3, "blocked", [{"status": "completed", "conclusion": "failure"}]),  # ci_failing
        ]:
            pr = dict(base, mergeable_state=state)
            classification = cc.classify_pr(pr, checks, now_value="2026-10-09T20:00:00+00:00")
            cc.record_pr_state(self.conn, "cloud", "zCloud", number, pr, classification)
        unfinished = cc.open_unfinished_work(self.conn, "cloud")
        labels = [item["label"] for item in unfinished]
        self.assertNotIn("mergeable", labels)
        self.assertEqual(labels[0], "conflicting")  # conflicting ranks before ci_failing

    def test_drop_missing_pr_state_removes_closed_prs_only(self):
        pr = {"draft": False, "mergeable_state": "dirty", "updated_at": "2026-10-09T19:00:00Z"}
        classification = cc.classify_pr(pr, [])
        cc.record_pr_state(self.conn, "cloud", "zCloud", 1, pr, classification)
        cc.record_pr_state(self.conn, "cloud", "zCloud", 2, pr, classification)
        removed = cc.drop_missing_pr_state(self.conn, "cloud", "zCloud", open_pr_numbers=[1])
        self.assertEqual(removed, 1)
        remaining = {row["pr_number"] for row in self.conn.execute("SELECT pr_number FROM pr_state").fetchall()}
        self.assertEqual(remaining, {1})

    def test_mergeable_prs_only_returns_auto_merge_eligible_rows(self):
        green = {"draft": False, "mergeable_state": "clean", "updated_at": "2026-10-09T19:00:00Z"}
        classification = cc.classify_pr(green, [{"status": "completed", "conclusion": "success"}], now_value="2026-10-09T20:00:00+00:00")
        cc.record_pr_state(self.conn, "cloud", "zCloud", 1, green, classification)
        ready = cc.mergeable_prs(self.conn, "cloud")
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["pr_number"], 1)


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()

    def test_material_completion_rate_counts_started_and_completed(self):
        cc.record_task_started(self.conn, "cloud")
        cc.record_task_started(self.conn, "cloud")
        cc.record_task_completed(self.conn, "cloud")
        rate = cc.material_completion_rate(self.conn, "cloud")
        self.assertEqual(rate["tasks_started"], 2)
        self.assertEqual(rate["tasks_completed"], 1)
        self.assertEqual(rate["material_completion_rate"], 0.5)

    def test_material_completion_rate_is_none_when_nothing_started(self):
        rate = cc.material_completion_rate(self.conn, "cloud")
        self.assertIsNone(rate["material_completion_rate"])

    def test_record_progress_event_updates_last_material_progress_only_for_material_kinds(self):
        cc.record_progress_event(self.conn, "cloud", "ci_cancelled")
        row = self.conn.execute("SELECT * FROM completion_metrics WHERE project_id='cloud'").fetchone()
        self.assertIsNone(row["last_material_progress_at"])
        cc.record_progress_event(self.conn, "cloud", "merge")
        row = self.conn.execute("SELECT * FROM completion_metrics WHERE project_id='cloud'").fetchone()
        self.assertIsNotNone(row["last_material_progress_at"])

    def test_record_progress_event_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            cc.record_progress_event(self.conn, "cloud", "not_a_real_kind")

    def test_stagnation_report_flags_projects_past_threshold(self):
        cc.record_progress_event(self.conn, "cloud", "merge")
        self.conn.execute(
            "UPDATE completion_metrics SET last_material_progress_at=? WHERE project_id='cloud'",
            ("2026-10-01T00:00:00+00:00",),
        )
        report = cc.stagnation_report(self.conn, threshold_seconds=3600, now_value="2026-10-09T20:00:00+00:00")
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["project_id"], "cloud")

    def test_stagnation_report_includes_projects_with_no_progress_ever(self):
        cc._ensure_metrics_row(self.conn, "neverstarted")
        report = cc.stagnation_report(self.conn, now_value="2026-10-09T20:00:00+00:00")
        self.assertIn("neverstarted", [item["project_id"] for item in report])


class CompletionStatusTests(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()

    def test_completion_status_shapes_full_dashboard_payload(self):
        pr = {"draft": False, "mergeable_state": "dirty", "updated_at": "2026-10-09T19:00:00Z"}
        classification = cc.classify_pr(pr, [])
        cc.record_pr_state(self.conn, "cloud", "zCloud", 580, pr, classification)
        cc.record_task_started(self.conn, "cloud")
        status = cc.completion_status(self.conn)
        self.assertIn("cloud", status["projects"])
        self.assertEqual(status["projects"]["cloud"]["unfinished_count"], 1)
        self.assertIn("portfolio", status)


class CompletionQueueCriteriaTests(unittest.TestCase):
    def test_conflicting_pr_gets_rebase_instruction(self):
        row = {"label": "conflicting", "pr_number": 580, "reason": "merge conflicts", "html_url": "https://x/580"}
        text = cc.completion_queue_criteria("zCloud", row)
        self.assertIn("#580", text)
        self.assertIn("Rebase", text)
        self.assertIn("Do not create a new pull request", text)


if __name__ == "__main__":
    unittest.main()
