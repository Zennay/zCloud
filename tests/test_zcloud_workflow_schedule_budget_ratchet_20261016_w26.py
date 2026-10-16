"""Adversarial source-only regression for scheduled GitHub Actions cost safety."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_workflow_schedule_budget_ratchet_20261016_w26 import (
    AdmissionError, audit, cross_schedule_bursts, field_values, is_high_frequency, schedules,
)

def workflow(expr: str) -> str:
    return "name: scheduled\non:\n  schedule:\n    - cron: '" + expr + "'\njobs:\n  check:\n    runs-on: ubuntu-latest\n"


class CronPolicyTests(unittest.TestCase):
    def test_unsafe_every_five_minutes(self):
        self.assertTrue(is_high_frequency("*/5 * * * *"))

    def test_short_burst(self):
        self.assertTrue(is_high_frequency("10,20 * * * *"))

    def test_safe_quarter_hour(self):
        self.assertFalse(is_high_frequency("*/15 * * * *"))

    def test_safe_daily(self):
        self.assertFalse(is_high_frequency("0 8 * * 1-5"))

    def test_safe_midnight(self):
        self.assertFalse(is_high_frequency("0 0,23 * * *"))

    def test_safe_hourly(self):
        self.assertFalse(is_high_frequency("0 * * * *"))

    def test_cron_range_with_step(self):
        self.assertEqual(field_values("8-19/5", 0, 59), {8, 13, 18})

    def test_five_field_required(self):
        with self.assertRaises(AdmissionError):
            is_high_frequency("@hourly")

    def test_zero_step_denied(self):
        with self.assertRaises(AdmissionError):
            is_high_frequency("*/0 * * * *")

    def test_out_of_bounds_denied(self):
        with self.assertRaises(AdmissionError):
            is_high_frequency("60 * * * *")

    def test_dynamic_cron_denied(self):
        with self.assertRaises(AdmissionError):
            schedules('on:\n  schedule:\n    - cron: ${{ inputs.cron }}')

    def test_duplicate_schedule_denied(self):
        with self.assertRaises(AdmissionError):
            schedules(workflow("0 9 * * *").replace("jobs:", "  schedule:\n    - cron: '0 9 * * *'\njobs:"))

    def test_ordinary_non_schedule(self):
        self.assertEqual(schedules("on:\n  pull_request:\n    branches: [main]\n"), ())

    def test_named_weekdays_and_months(self):
        self.assertFalse(is_high_frequency("30 8 * JAN-MAR MON-FRI"))
        self.assertTrue(is_high_frequency("*/5 10 * JUN TUE"))

    def test_inline_event_alias_refused(self):
        with self.assertRaises(AdmissionError):
            schedules("on: *events\njobs:\n  test:\n    runs-on: ubuntu-latest\n")

    def test_inline_event_list_allowed(self):
        self.assertEqual(schedules("on: [push, pull_request]\n"), ())

    def test_inline_schedule_mapping_refused(self):
        with self.assertRaises(AdmissionError):
            schedules("on: {schedule: [{cron: '*/5 * * * *'}]}\n")

    def test_four_space_event_block_detected(self):
        self.assertEqual(schedules("on:\n    schedule:\n        - cron: '*/5 * * * *'\n"),
                         ("*/5 * * * *",))
        self.assertTrue(is_high_frequency(schedules("on:\n    schedule:\n        - cron: '*/5 * * * *'\n")[0]))

    def test_invalid_event_indentation_denied(self):
        with self.assertRaises(AdmissionError):
            schedules("on:\n    push:\n  schedule:\n    - cron: '*/5 * * * *'\n")

    def test_two_hourly_entries_five_minutes_apart(self):
        pair = ("0 * * * *", "5 * * * *")
        self.assertEqual(cross_schedule_bursts(pair), {pair})

    def test_disjoint_weekdays_not_false_positive(self):
        self.assertFalse(cross_schedule_bursts(
            ("0 10 * * MON", "5 10 * * TUE")))

    def test_same_weekday_entry_pair_denied(self):
        self.assertTrue(cross_schedule_bursts(
            ("0 10 * * MON", "5 10 * * MON")))

    def test_two_months_not_false_positive(self):
        self.assertFalse(cross_schedule_bursts(
            ("0 10 10 JAN *", "5 10 10 FEB *")))

    def test_cross_midnight_burst(self):
        self.assertTrue(cross_schedule_bursts(
            ("55 23 * * *", "5 0 * * *")))

    def test_cross_midnight_fifteen_minutes_allowed(self):
        self.assertFalse(cross_schedule_bursts(
            ("45 23 * * *", "0 0 * * *")))

    def test_cross_midnight_disjoint_weekdays(self):
        self.assertFalse(cross_schedule_bursts(
            ("55 23 * * MON", "5 0 * * WED")))

    def test_leap_day_across_midnight(self):
        self.assertTrue(cross_schedule_bursts(
            ("55 23 28 FEB *", "5 0 29 FEB *")))

    def test_day_of_month_or_weekday_semantics(self):
        # 1st of a month can be a Tuesday, despite the Monday alternative.
        self.assertTrue(cross_schedule_bursts(
            ("0 9 1 * MON", "5 9 * * TUE")))

    def test_cross_entry_bounded(self):
        with self.assertRaises(AdmissionError):
            cross_schedule_bursts(("0 9 * * *",) * 33)

    def test_weekly_midnight_is_not_a_daily_burst(self):
        self.assertFalse(is_high_frequency("5,55 0,23 * * MON"))

    def test_daily_midnight_is_a_real_burst(self):
        self.assertTrue(is_high_frequency("5,55 0,23 * * *"))

    def test_consecutive_selected_weekdays_cross_midnight(self):
        self.assertTrue(is_high_frequency("5,55 0,23 * * MON,TUE"))

    def test_monthly_midnight_does_not_invent_next_day(self):
        self.assertFalse(is_high_frequency("5,55 0,23 1 * *"))

    def test_nontrivial_day_steps_are_restricted(self):
        from scripts.zcloud_workflow_schedule_budget_ratchet_20261016_w26 import (
            _calendar_matches, _compiled_cron,
        )
        expr = _compiled_cron("0 9 */2 * MON")
        self.assertFalse(expr["dom_wild"])
        self.assertTrue(_calendar_matches(expr, 1, 3, 3))
        self.assertFalse(_calendar_matches(expr, 1, 2, 3))
        self.assertTrue(_calendar_matches(expr, 1, 2, 1))

    def test_timezone_merge_alias_not_allowed(self):
        with self.assertRaises(AdmissionError):
            schedules("on:\n  schedule:\n    - cron: '0 9 * * *'\n      <<: *hidden_timezone\n")

    def test_empty_timezone_selector_denied(self):
        with self.assertRaises(AdmissionError):
            schedules("on:\n  schedule:\n    - cron: '0 9 * * *'\n      timezone:\n")

    def test_duplicate_on_key_denied_instead_of_trusting_last(self):
        with self.assertRaises(AdmissionError):
            schedules("on: push\non:\n  schedule:\n    - cron: '*/5 * * * *'\n")

    def test_quoted_duplicate_on_key_denied(self):
        with self.assertRaises(AdmissionError):
            schedules("on: pull_request\n'on': workflow_dispatch\n")

    def test_explicit_yaml_mapping_key_denied(self):
        with self.assertRaises(AdmissionError):
            schedules("? on\n: {schedule: [{cron: '*/5 * * * *'}]}\n")

    def test_top_level_merge_cannot_inject_hidden_schedule(self):
        with self.assertRaises(AdmissionError):
            schedules("name: sample\n<<: *scheduled_events\njobs:\n  test:\n    runs-on: ubuntu-latest\n")

    def test_quoted_top_level_merge_also_denied(self):
        with self.assertRaises(AdmissionError):
            schedules("name: sample\n'<<': *scheduled_events\non: push\n")

    def test_yaml_merge_cannot_hide_schedule_trigger(self):
        with self.assertRaises(AdmissionError):
            schedules("on:\n  <<: *schedule_alias\njobs:\n  test:\n    runs-on: ubuntu-latest\n")

    def test_anchor_cannot_replace_event_approval(self):
        with self.assertRaises(AdmissionError):
            schedules("on: &event_alias\n  schedule:\n    - cron: '*/5 * * * *'\n")

    def test_timezone_metadata_parsed(self):
        zones = []
        expr = "on:\n  schedule:\n    - cron: '0 9 * * *'\n      timezone: America/New_York\n"
        self.assertEqual(schedules(expr, timezone_metadata=zones), ("0 9 * * *",))
        self.assertEqual(zones, ["timezone: America/New_York"])

    def test_timezone_not_in_unrelated_jobs(self):
        zones = []
        expr = "on:\n  push:\n    branches: [main]\njobs:\n  test:\n    timezone: Asia/Tokyo\n"
        self.assertEqual(schedules(expr, timezone_metadata=zones), ())
        self.assertEqual(zones, [])

    def test_valid_schedule_comment_and_quoted_cron_inline_comment(self):
        source = ('on:\n  "schedule": # nightly interval\n'
                  '    - "cron": "*/5 * * * *" # accepted YAML comment\n')
        self.assertEqual(schedules(source), ("*/5 * * * *",))

    def test_unquoted_cron_value_with_yaml_comment(self):
        source = 'on:\n  schedule: # routine\n    - cron: 0 9 * * * # daily\n'
        self.assertEqual(schedules(source), ("0 9 * * *",))

    def test_comment_does_not_authorize_inline_event(self):
        with self.assertRaises(AdmissionError):
            schedules('on:\n  schedule: *hidden # alias\n')

    def test_duplicate_quoted_cron_cannot_override_safe_cron(self):
        source = 'on:\n  schedule:\n    - cron: "0 9 * * *"\n      "cron": "*/5 * * * *"\n'
        with self.assertRaises(AdmissionError):
            schedules(source)

    def test_duplicate_quoted_timezone_cannot_override_safe_zone(self):
        source = 'on:\n  schedule:\n    - cron: "0 9 * * *"\n      timezone: UTC\n      "timezone": America/New_York\n'
        with self.assertRaises(AdmissionError):
            schedules(source)

    def test_timezone_without_entry_rejected(self):
        source = 'on:\n  schedule:\n      timezone: America/New_York\n    - cron: "0 9 * * *"\n'
        with self.assertRaises(AdmissionError):
            schedules(source)

    def test_unknown_schedule_item_field_rejected(self):
        source = 'on:\n  schedule:\n    - cron: "0 9 * * *"\n      "hidden": *unsafe_cron\n'
        with self.assertRaises(AdmissionError):
            schedules(source)

    def test_cron_must_share_indent_between_entries(self):
        source = 'on:\n  schedule:\n    - cron: "0 9 * * *"\n      - cron: "*/5 * * * *"\n'
        with self.assertRaises(AdmissionError):
            schedules(source)

    def test_multiple_zoned_schedule_entries_supported(self):
        zones = []
        source = ('on:\n  schedule:\n'
                  '    - cron: "0 9 * * *"\n      timezone: UTC\n'
                  '    - "cron": "0 10 * * *"\n      "timezone": Europe/Amsterdam\n')
        self.assertEqual(schedules(source, timezone_metadata=zones),
                         ("0 9 * * *", "0 10 * * *"))
        self.assertEqual(zones, ["timezone: UTC", "timezone: Europe/Amsterdam"])

    def test_comment_and_blank_between_cron_entries_supported(self):
        source = ('on:\n  schedule:\n    - cron: "0 9 * * *"\n\n'
                  '    # another time\n    - cron: "0 10 * * *"\n')
        self.assertEqual(schedules(source), ("0 9 * * *", "0 10 * * *"))

    def test_double_quoted_schedule_event_and_cron_key(self):
        source = 'on:\n  "schedule":\n    - "cron": "*/5 * * * *"\n'
        self.assertEqual(schedules(source), ("*/5 * * * *",))
        self.assertTrue(is_high_frequency(schedules(source)[0]))

    def test_single_quoted_schedule_event_and_cron_key(self):
        source = "on:\n  'schedule':\n    - 'cron': '*/2 * * * *'\n"
        self.assertEqual(schedules(source), ("*/2 * * * *",))

    def test_quoted_timezone_key_and_canonical_metadata(self):
        zones = []
        source = 'on:\n  schedule:\n    - cron: "0 9 * * *"\n      "timezone": America/New_York\n'
        self.assertEqual(schedules(source, timezone_metadata=zones), ("0 9 * * *",))
        self.assertEqual(zones, ["timezone: America/New_York"])

    def test_quoted_non_schedule_event_does_not_get_rejected(self):
        self.assertEqual(schedules('on:\n  "pull_request":\n    branches: [main]\n'), ())

    def test_escaped_event_key_cannot_hide_schedule(self):
        with self.assertRaises(AdmissionError):
            schedules('on:\n  "sch\\u0065dule":\n    - cron: "*/5 * * * *"\n')

    def test_escaped_root_on_key_cannot_hide_schedule(self):
        with self.assertRaises(AdmissionError):
            schedules('"\\u006fn":\n  schedule:\n    - cron: "*/5 * * * *"\n')

    def test_utf8_bom_does_not_hide_schedule_trigger(self):
        self.assertEqual(schedules('\ufeffon:\n  schedule:\n    - cron: "*/5 * * * *"\n'),
                         ("*/5 * * * *",))

    def test_quoted_timezone_selector_alias_rejected(self):
        with self.assertRaises(AdmissionError):
            schedules('on:\n  schedule:\n    - cron: "0 9 * * *"\n      "timezone": *zone_alias\n')

    def test_quoted_schedule(self):
        self.assertEqual(schedules(workflow("30 8 * * 1-5")), ("30 8 * * 1-5",))


class RealGitNoNewDebtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / ".github/workflows"
        self.directory.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI Fixture")
        self.write("legacy.yml", "*/5 * * * *")
        self.write("safe.yml", "0 8 * * *")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args],
                                text=True, capture_output=True, check=True)
        return result.stdout.strip()

    def write(self, name, expr):
        (self.directory / name).write_text(workflow(expr), encoding="utf-8")

    def test_unchanged_legacy_allowed(self):
        receipt = audit(self.root, self.baseline)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(len(receipt["existing_high_frequency"]), 1)
        self.assertFalse(receipt["production_mutation_authorized"])

    def test_new_unsafe_workflow_denied(self):
        self.write("new.yml", "*/5 * * * *")
        receipt = audit(self.root, self.baseline)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["new_high_frequency"][0]["workflow"],
                         ".github/workflows/new.yml")

    def test_edit_to_fast_schedule_denied(self):
        self.write("safe.yml", "*/2 * * * *")
        receipt = audit(self.root, self.baseline)
        self.assertFalse(receipt["accepted"])

    def test_legacy_safe_schedule_rewrite_allowed(self):
        self.write("legacy.yml", "0 8 * * *")
        receipt = audit(self.root, self.baseline)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["existing_high_frequency"], [])

    def test_only_step_edits_do_not_add_debt(self):
        path = self.directory / "legacy.yml"
        path.write_text(path.read_text(encoding="utf-8") + "\n# Step-only edit\n")
        self.assertTrue(audit(self.root, self.baseline)["accepted"])

    def test_new_cross_entry_burst_denied(self):
        path = self.directory / "safe.yml"
        path.write_text(workflow("0 * * * *").replace(
            "jobs:", "    - cron: '5 * * * *'\njobs:"))
        receipt = audit(self.root, self.baseline)
        self.assertFalse(receipt["accepted"])
        self.assertTrue(any(item.get("reason") == "cross_schedule_burst"
                            for item in receipt["new_high_frequency"]))

    def test_quoted_high_frequency_with_trailing_comment_denied(self):
        path = self.directory / "safe.yml"
        path.write_text('on:\n  "schedule": # audit me\n'
                        '    - "cron": "*/5 * * * *" # expensive\n',
                        encoding="utf-8")
        result = audit(self.root, self.baseline)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["new_high_frequency"][0]["cron"], "*/5 * * * *")

    def test_quoted_override_is_denied_in_real_git_audit(self):
        path = self.directory / "safe.yml"
        path.write_text('on:\n  schedule:\n    - cron: "0 9 * * *"\n'
                        '      "cron": "*/5 * * * *"\n'
                        'jobs:\n  check:\n    runs-on: ubuntu-latest\n',
                        encoding="utf-8")
        with self.assertRaises(AdmissionError):
            audit(self.root, self.baseline)

    def test_quoted_cron_schedule_is_denied_in_a_real_repo(self):
        path = self.directory / "safe.yml"
        path.write_text('on:\n  "schedule":\n    - "cron": "*/5 * * * *"\n'
                        'jobs:\n  check:\n    runs-on: ubuntu-latest\n',
                        encoding="utf-8")
        result = audit(self.root, self.baseline)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["new_high_frequency"][0]["cron"], "*/5 * * * *")

    def test_quoted_timezone_requires_review_in_real_repo(self):
        path = self.directory / "safe.yml"
        path.write_text(workflow("0 8 * * *").replace(
            "jobs:", '      "timezone": America/New_York\njobs:'),
            encoding="utf-8")
        result = audit(self.root, self.baseline)
        self.assertFalse(result["accepted"])
        self.assertTrue(any(entry.get("reason") == "timezone_requires_independent_review"
                            for entry in result["new_high_frequency"]))

    def test_existing_duplicate_pair_does_not_approve_third_duplicate(self):
        path = self.directory / "safe.yml"
        double = workflow("0 9 * * *").replace(
            "jobs:", "    - cron: '0 9 * * *'\njobs:")
        path.write_text(double, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "legacy duplicate pair")
        baseline = self.git("rev-parse", "HEAD")
        original = audit(self.root, baseline)
        self.assertTrue(original["accepted"])
        path.write_text(double.replace(
            "jobs:", "    - cron: '0 9 * * *'\njobs:"), encoding="utf-8")
        receipt = audit(self.root, baseline)
        self.assertFalse(receipt["accepted"])
        collisions = [row for row in receipt["new_high_frequency"]
                      if row.get("reason") == "cross_schedule_burst"]
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["baseline_instances"], 1)
        self.assertEqual(collisions[0]["candidate_instances"], 3)

    def test_reordered_existing_duplicate_schedule_is_safe(self):
        path = self.directory / "safe.yml"
        path.write_text(workflow("0 9 * * *").replace(
            "jobs:", "    - cron: '0 9 * * *'\njobs:"), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "existing duplicate")
        baseline = self.git("rev-parse", "HEAD")
        self.assertTrue(audit(self.root, baseline)["accepted"])
        path.write_text(path.read_text() + "\n# unrelated source comment\n",
                        encoding="utf-8")
        self.assertTrue(audit(self.root, baseline)["accepted"])

    def test_legacy_pair_grandfathered_exactly(self):
        legacy = self.directory / "legacy.yml"
        legacy.write_text(workflow("0 * * * *").replace(
            "jobs:", "    - cron: '5 * * * *'\njobs:"))
        self.git("add", ".")
        self.git("commit", "-qm", "preexisting two-entry legacy")
        base = self.git("rev-parse", "HEAD")
        self.assertTrue(audit(self.root, base)["accepted"])
        legacy.write_text(legacy.read_text() + "\n# unrelated step")
        self.assertTrue(audit(self.root, base)["accepted"])

    def test_new_pair_not_hidden_by_legacy_pair(self):
        path = self.directory / "legacy.yml"
        path.write_text(workflow("0 * * * *").replace(
            "jobs:", "    - cron: '5 * * * *'\njobs:"))
        self.git("add", ".")
        self.git("commit", "-qm", "legacy source pair")
        base = self.git("rev-parse", "HEAD")
        path.write_text(path.read_text().replace(
            "jobs:", "    - cron: '10 * * * *'\njobs:"))
        result = audit(self.root, base)
        self.assertFalse(result["accepted"])
        self.assertTrue(any("10 * * * *" in item["cron"]
                            for item in result["new_high_frequency"]))

    def test_new_timezone_requires_independent_review(self):
        path = self.directory / "safe.yml"
        path.write_text(workflow("0 8 * * *").replace(
            "jobs:", "      timezone: America/New_York\njobs:"))
        receipt = audit(self.root, self.baseline)
        self.assertFalse(receipt["accepted"])
        self.assertTrue(any(entry.get("reason") == "timezone_requires_independent_review"
                            for entry in receipt["new_high_frequency"]))

    def test_legacy_timezone_preserved_with_unrelated_edit(self):
        path = self.directory / "safe.yml"
        path.write_text(workflow("0 8 * * *").replace(
            "jobs:", "      timezone: America/New_York\njobs:"))
        self.git("add", ".")
        self.git("commit", "-qm", "grandfather known timezone")
        base = self.git("rev-parse", "HEAD")
        self.assertTrue(audit(self.root, base)["accepted"])
        path.write_text(path.read_text() + "\n# Source-only comment\n")
        self.assertTrue(audit(self.root, base)["accepted"])

    def test_removing_existing_timezone_requires_review(self):
        path = self.directory / "safe.yml"
        zoned = workflow("0 8 * * *").replace(
            "jobs:", "      timezone: America/New_York\njobs:")
        path.write_text(zoned, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "existing zoned schedule")
        base = self.git("rev-parse", "HEAD")
        path.write_text(workflow("0 8 * * *"), encoding="utf-8")
        receipt = audit(self.root, base)
        self.assertFalse(receipt["accepted"])
        self.assertTrue(any(
            item.get("reason") == "timezone_requires_independent_review"
            for item in receipt["new_high_frequency"]
        ))

    def test_existing_timezone_cron_mutation_denied(self):
        path = self.directory / "safe.yml"
        path.write_text(workflow("0 8 * * *").replace(
            "jobs:", "      timezone: America/New_York\njobs:"))
        self.git("add", ".")
        self.git("commit", "-qm", "existing zoned schedule")
        base = self.git("rev-parse", "HEAD")
        path.write_text(path.read_text().replace("0 8 * * *", "0 9 * * *"))
        self.assertFalse(audit(self.root, base)["accepted"])

    def test_missing_or_invalid_baseline_denied(self):
        with self.assertRaises(AdmissionError):
            audit(self.root, "main")

    def test_symlinked_workflow_denied(self):
        (self.directory / "alias.yml").symlink_to(self.directory / "legacy.yml")
        with self.assertRaises(AdmissionError):
            audit(self.root, self.baseline)

    def test_symlinked_workflow_directory_denied(self):
        self.directory.rename(self.root / ".github/workflows-original")
        self.directory.symlink_to(self.root / ".github/workflows-original", target_is_directory=True)
        with self.assertRaises(AdmissionError):
            audit(self.root, self.baseline)

    def test_symlinked_github_parent_denied(self):
        self.directory.parent.rename(self.root / "github-original")
        (self.root / ".github").symlink_to(self.root / "github-original", target_is_directory=True)
        with self.assertRaises(AdmissionError):
            audit(self.root, self.baseline)

    def test_outside_workflow_directory_not_seen(self):
        (self.root / "README.md").write_text("*/1 * * * *")
        self.assertEqual(audit(self.root, self.baseline)["workflow_count"], 2)


if __name__ == "__main__":
    unittest.main()
