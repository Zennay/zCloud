"""Local offline safety tests of GitHub-hosted queue operator reporting."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from scripts import zcloud_watchdog_operator_summary_20261014_w1 as reporter

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_watchdog_operator_summary_20261014_w1.py"


def report(decision="NOOP", **updates):
    data = {
        "policy": reporter.POLICY,
        "decision": decision,
        "mutation_authorized": False,
        "production_verified": False,
        "inventory_complete": False,
        "cancel_run_ids": [],
        "observed_run_count": 2 if decision == "REVIEW_ONLY" else 0,
        "candidate_duplicate_groups": [[1, 2]] if decision == "REVIEW_ONLY" else [],
    }
    data.update(updates)
    return json.dumps(data).encode("utf-8")


class TestHostedQueueSummary(unittest.TestCase):
    def test_valid_noop_produces_non_authorizing_dutch_summary(self):
        markdown, status = reporter.summarize(report(), 0)
        self.assertEqual(status, 0)
        self.assertIn("**NOOP**", markdown)
        self.assertIn("**NIET TOEGESTAAN**", markdown)
        self.assertIn("**NIET BEWEZEN**", markdown)
        self.assertNotIn("geautoriseerd", markdown.lower())

    def test_review_only_is_terminal_non_green(self):
        markdown, status = reporter.summarize(report("REVIEW_ONLY"), 1)
        self.assertEqual(status, 1)
        self.assertIn("**REVIEW_ONLY**", markdown)

    def test_explicit_deny_is_terminal_non_green(self):
        markdown, status = reporter.summarize(report("DENY"), 1)
        self.assertEqual(status, 1)
        self.assertIn("**DENY**", markdown)

    def test_wrong_exit_or_external_timeout_cannot_publish_green(self):
        for code in (1, 2, 124, 137, True, -1):
            with self.subTest(code=code):
                markdown, status = reporter.summarize(report(), code)
                self.assertEqual(status, 1)
                self.assertIn("**DENY**", markdown)

    def test_privilege_flags_forge_denied(self):
        for key, value in (
            ("mutation_authorized", True),
            ("production_verified", True),
            ("inventory_complete", True),
            ("cancel_run_ids", [999]),
        ):
            with self.subTest(key=key):
                markdown, status = reporter.summarize(report(**{key: value}), 0)
                self.assertEqual(status, 1)
                self.assertIn("**DENY**", markdown)

    def test_missing_key_denied(self):
        for key in ("policy", "decision", "mutation_authorized",
                    "production_verified", "inventory_complete", "cancel_run_ids"):
            obj = json.loads(report())
            obj.pop(key)
            self.assertEqual(reporter.summarize(json.dumps(obj).encode(), 0)[1], 1)

    def test_duplicate_json_keys_denied(self):
        raw = report()[:-1] + b',"decision":"NOOP"}'
        self.assertEqual(reporter.summarize(raw, 0)[1], 1)

    def test_non_finite_json_and_unknown_policy_denied(self):
        obj = report()[:-1] + b',"extra":NaN}'
        self.assertEqual(reporter.summarize(obj, 0)[1], 1)
        self.assertEqual(reporter.summarize(report(policy="other"), 0)[1], 1)

    def test_oversized_and_invalid_utf8_denied(self):
        for payload in (b"", b"bad", b"\xff", b"x" * 32768):
            self.assertEqual(reporter.summarize(payload, 0)[1], 1)

    def test_report_cannot_expose_untrusted_secret_fields(self):
        raw = report(reason="supersecret_internal_token", raw_prompt="private prompt")
        markdown, status = reporter.summarize(raw, 0)
        self.assertEqual(status, 0)
        self.assertNotIn("supersecret_internal_token", markdown)
        self.assertNotIn("private prompt", markdown)


    def test_safe_dutch_reason_categories_are_shown_without_raw_error_text(self):
        expected = {
            "incomplete_queue_pages": "geen complete",
            "invalid_job_identity": "identiteit",
            "job_state_changed": "veranderde",
            "queue_too_large": "meer wachtrijgegevens",
            "main_changed": "hoofdbranch",
            "observation_too_slow": "te lang",
            "github_inventory_untrusted": "niet betrouwbaar",
        }
        for code, fragment in expected.items():
            with self.subTest(code=code):
                markdown, exit_status = reporter.summarize(report("DENY", reason=code), 1)
                self.assertEqual(exit_status, 1)
                self.assertIn(fragment, markdown)
                self.assertNotIn(code, markdown)

    def test_unrecognized_reason_and_control_chars_never_reach_markdown(self):
        for injected in ("token=secret-file", "bad\\n- **APPROVED**", "private prompt",
                         "cross_origin_next_link", None, ["not", "a", "code"]):
            with self.subTest(value=injected):
                markdown, status = reporter.summarize(report("DENY", reason=injected), 1)
                self.assertEqual(status, 1)
                self.assertIn("niet betrouwbaar", markdown)
                self.assertNotIn(str(injected), markdown)
                self.assertNotIn("APPROVED", markdown)

    def test_green_observation_does_not_present_production_green(self):
        markdown, status = reporter.summarize(report("NOOP", reason="irrelevant"), 0)
        self.assertEqual(status, 0)
        self.assertIn("Geen mogelijke dubbele", markdown)
        self.assertIn("**NIET BEWEZEN**", markdown)
        self.assertNotIn("Productie gezond", markdown)


    def test_valid_review_reports_only_bounded_group_counts(self):
        markdown, status = reporter.summarize(
            report("REVIEW_ONLY", observed_run_count=7,
                   candidate_duplicate_groups=[[4, 5], [6, 7]]), 1
        )
        self.assertEqual(status, 1)
        self.assertIn("wachtrijruns: **7**", markdown)
        self.assertIn("groepen ter beoordeling: **2**", markdown)
        self.assertNotIn("[4, 5]", markdown)
        self.assertIn("geen bevestiging van productiegezondheid", markdown)

    def test_invalid_or_contradictory_report_counts_never_displayed(self):
        cases = (
            {"observed_run_count": -1},
            {"observed_run_count": True},
            {"observed_run_count": "2"},
            {"observed_run_count": 501},
            {"candidate_duplicate_groups": [[1, 2]]},
            {"candidate_duplicate_groups": "secret-list"},
        )
        for change in cases:
            with self.subTest(change=change):
                markdown, code = reporter.summarize(report(**change), 0)
                self.assertEqual(code, 1)
                self.assertIn("**DENY**", markdown)
                self.assertNotIn("Aangeleverde wachtrijruns", markdown)
                self.assertNotIn("secret-list", markdown)

    def test_invalid_review_groups_and_mismatched_counts_fail_closed(self):
        cases = (
            {"candidate_duplicate_groups": []},
            {"candidate_duplicate_groups": [[1]]},
            {"candidate_duplicate_groups": [[1, True]]},
            {"candidate_duplicate_groups": [[1, 1]]},
            {"candidate_duplicate_groups": [[1, 2], [2, 3]]},
            {"observed_run_count": 1},
        )
        for change in cases:
            with self.subTest(change=change):
                markdown, code = reporter.summarize(report("REVIEW_ONLY", **change), 1)
                self.assertEqual(code, 1)
                self.assertIn("**DENY**", markdown)
                self.assertNotIn("Aangeleverde wachtrijruns", markdown)

    def test_deny_report_never_uses_partial_or_hostile_counts(self):
        markdown, status = reporter.summarize(
            report("DENY", observed_run_count=500,
                   candidate_duplicate_groups=[[1, 2]],
                   reason="invalid_job_identity"), 1
        )
        self.assertEqual(status, 1)
        self.assertIn("**DENY**", markdown)
        self.assertNotIn("Aangeleverde wachtrijruns", markdown)

    def test_actual_cli_writes_only_sanitized_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "queue.json"
            summary = folder / "summary.md"
            source.write_bytes(report("REVIEW_ONLY", reason="secretvalue"))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--report", str(source),
                 "--collector-exit", "1", "--summary-path", str(summary)],
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stdout, "")
            self.assertIn("REVIEW_ONLY", summary.read_text(encoding="utf-8"))
            self.assertNotIn("secretvalue", summary.read_text(encoding="utf-8"))

    def test_actual_cli_missing_report_denies_without_throwing(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            summary = folder / "summary.md"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--report", str(folder / "missing.json"),
                 "--collector-exit", "0", "--summary-path", str(summary)],
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("DENY", summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
