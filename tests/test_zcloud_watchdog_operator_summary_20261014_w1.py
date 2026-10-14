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
