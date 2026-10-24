"""Offline adversarial acceptance for an exact canonical dashboard summary gate.

No network, GitHub writes, VPS, SQLite, browser or runner mutation.
"""
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import zcloud_dashboard_external_probe_20261013_w1 as probe
import zcloud_dashboard_summary_guard_20261014_w1 as guard


def report(status="healthy", attempts=1, failures=None):
    failures = failures or {}
    result = probe.ProbeSummary(
        status, None, attempts, status == "healthy" and attempts > 1, failures
    )
    return (probe.operator_markdown(result) + "\n").encode("utf-8")


class CanonicalSummaryTests(unittest.TestCase):
    def test_accept_exact_reachable_and_recovered(self):
        for payload, rc in (
            (report(), 0),
            (report("healthy", 3, {"dns": 1, "timeout": 1}), 0),
            (report("unhealthy", 2, {"http_server_error": 1, "timeout": 1}), 1),
            (guard.TIMEOUT_REPORT.encode(), 124),
        ):
            with self.subTest(rc=rc, raw=payload):
                self.assertTrue(guard.validate_summary(payload, rc))

    def test_all_existing_probe_failure_categories_have_unambiguous_text(self):
        for category in sorted(probe.CATEGORIES - {"healthy"}):
            with self.subTest(category=category):
                candidate = report("unhealthy", 1, {category: 1})
                self.assertTrue(guard.validate_summary(candidate, 1))
                self.assertFalse(guard.validate_summary(candidate, 0))

    def test_real_probe_cli_renderer_can_be_validated_offline(self):
        # The actual probe CLI renders the exact bytes that the operator guard
        # admits, not an independently hand-built approximation.
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        for status, failures, rc in (
            ("healthy", {}, 0),
            ("healthy", {"dns": 1, "connection": 1}, 0),
            ("unhealthy", {"timeout": 2}, 1),
        ):
            with self.subTest(status=status, failures=failures):
                attempts = sum(failures.values()) + (status == "healthy")
                summary = probe.ProbeSummary(
                    status, None, attempts,
                    status == "healthy" and attempts > 1, failures,
                )
                stream = io.StringIO()
                with mock.patch.object(probe, "run_probe", return_value=summary):
                    with redirect_stdout(stream):
                        self.assertEqual(probe.main(["--format", "markdown"]), rc)
                self.assertTrue(guard.validate_summary(stream.getvalue().encode("utf-8"), rc))

    def test_guard_source_has_no_recovery_or_transport_capability(self):
        source = (ROOT / "scripts/zcloud_dashboard_summary_guard_20261014_w1.py").read_text()
        for forbidden in ("import urllib", "import requests", "import sqlite3",
                          "import subprocess", "systemctl", "method=\\\"POST\\\"",
                          "os.system(", "subprocess.run("):
            self.assertNotIn(forbidden, source)

    def test_reject_noncanonical_or_malformed_reports(self):
        valid = report()
        samples = [
            b"", b"x" * 2049, b"\xff", valid + b"X-Secret: token\n",
            valid + b"**Status:** API bereikbaar\n",
            valid.replace(b"\n", b"\r\n"),
            valid.replace(b"**Status:** API bereikbaar",
                          b"**Status:** API niet aantoonbaar bereikbaar"),
            valid.replace(b"**Controlepogingen:** 1", b"**Controlepogingen:** 01"),
            valid.replace(b"**Controlepogingen:** 1", b"**Controlepogingen:** 6"),
            valid.replace(b"**Hersteld na hercontrole:** nee",
                          b"**Hersteld na hercontrole:** ja"),
            valid.replace(b"**Bronvertrouwen:**", b"**Bronbewijs:**"),
            valid.replace(b"**Herstel of herstart wordt nooit automatisch uitgevoerd.**",
                          b"**Herstel automatisch uitgevoerd.**"),
            valid.replace(b"API bereikbaar", b"API bereikbaar <script>alert(1)</script>"),
            valid + b"\n",
            valid[:-1],
        ]
        for raw in samples:
            with self.subTest(raw=raw[:60]):
                self.assertFalse(guard.validate_summary(raw, 0))

    def test_reject_inconsistent_counts_and_duplicated_failure_categories(self):
        valid = report("healthy", 3, {"dns": 1, "timeout": 1})
        for old, new in (
            (b"DNS / naamoplossing: 1", b"DNS / naamoplossing: 2"),
            (b"**Controlepogingen:** 3", b"**Controlepogingen:** 2"),
            (b"**Hersteld na hercontrole:** ja", b"**Hersteld na hercontrole:** nee"),
            (b"DNS / naamoplossing: 1", b"Onbekende categorie: 1"),
            (b"- Time-out: 1", b"- Time-out: 1\n- Time-out: 1"),
            (b"- DNS / naamoplossing: 1\n- Time-out: 1",
             b"- Time-out: 1\n- DNS / naamoplossing: 1"),
        ):
            with self.subTest(old=old, new=new):
                self.assertIn(old, valid)
                self.assertFalse(guard.validate_summary(valid.replace(old, new), 0))

    def test_no_other_exit_codes_or_cross_status(self):
        for payload, rc in (
            (report(), 1),
            (report("unhealthy", 1, {"timeout": 1}), 0),
            (report(), 124),
            (guard.TIMEOUT_REPORT.encode(), 0),
            (guard.TIMEOUT_REPORT.encode(), 1),
            (report(), 2), (report(), -1), (report(), True),
            (report(), None), (report(), "0"),
        ):
            with self.subTest(rc=rc):
                self.assertFalse(guard.validate_summary(payload, rc))

    def test_timeout_note_must_match_exact_constant(self):
        note = guard.TIMEOUT_REPORT.encode()
        for altered in (note + b"SECRET", note[:-1], note.replace(b"Reden", b"Root"),
                        note.replace(b"niet aantoonbaar", b"zeker")):
            self.assertFalse(guard.validate_summary(altered, 124))

    def test_invalid_nonbytes_or_invalid_utf8_never_throw(self):
        for raw in (None, "", [], {}, bytearray(report()), b"\xff"):
            self.assertFalse(guard.validate_summary(raw, 0))

    def test_full_cli_echoes_only_accepted_report(self):
        for data, rc, expected in (
            (report(), 0, 0),
            (report("unhealthy", 1, {"connection": 1}), 1, 0),
            (guard.TIMEOUT_REPORT.encode(), 124, 0),
            (report() + b"SECRET_PRIVATE_URL", 0, 3),
            (b"SECRET_PRIVATE_URL", 1, 3),
            (report(), 7, 3),
            (b"Z" * (guard.MAX_SUMMARY_BYTES + 100), 0, 3),
        ):
            with self.subTest(code=rc, result=expected):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/zcloud_dashboard_summary_guard_20261014_w1.py"),
                     "--probe-rc", str(rc)],
                    input=data, capture_output=True, timeout=5, check=False,
                )
                self.assertEqual(proc.returncode, expected, proc.stderr)
                self.assertEqual(proc.stdout, data if expected == 0 else b"")
                self.assertNotIn(b"SECRET_PRIVATE_URL", proc.stderr)


if __name__ == "__main__":
    unittest.main()
