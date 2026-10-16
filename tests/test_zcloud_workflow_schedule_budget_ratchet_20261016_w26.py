"""Adversarial source-only regression for scheduled GitHub Actions cost safety."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_workflow_schedule_budget_ratchet_20261016_w26 import (
    AdmissionError, audit, field_values, is_high_frequency, schedules,
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

    def test_missing_or_invalid_baseline_denied(self):
        with self.assertRaises(AdmissionError):
            audit(self.root, "main")

    def test_symlinked_workflow_denied(self):
        (self.directory / "alias.yml").symlink_to(self.directory / "legacy.yml")
        with self.assertRaises(AdmissionError):
            audit(self.root, self.baseline)

    def test_outside_workflow_directory_not_seen(self):
        (self.root / "README.md").write_text("*/1 * * * *")
        self.assertEqual(audit(self.root, self.baseline)["workflow_count"], 2)


if __name__ == "__main__":
    unittest.main()
