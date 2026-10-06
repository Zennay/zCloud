import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_log_retention_audit import (
    RetentionAuditError,
    audit_repository,
    audit_workflow,
)

ROOT = Path(__file__).resolve().parents[1]


class LogRetentionAuditTests(unittest.TestCase):
    def test_detects_explicit_and_implicit_artifact_retention(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "explicit.yml").write_text(
                """
jobs:
  proof:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          name: receipt
          path: receipt.json
          retention-days: 14
""",
                encoding="utf-8",
            )
            (root / "implicit.yml").write_text(
                """
jobs:
  proof:
    steps:
      - name: Upload
        uses: actions/upload-artifact@v4
        with:
          name: receipt
          path: receipt.json
""",
                encoding="utf-8",
            )
            report = audit_repository(root)
        self.assertEqual(2, report["artifact_upload_count"])
        self.assertEqual(1, report["explicit_retention_count"])
        self.assertEqual(1, report["implicit_retention_count"])
        self.assertEqual(50.0, report["explicit_coverage_percent"])

    def test_multiple_or_invalid_retention_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "bad.yml"
            path.write_text(
                """
jobs:
  proof:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          retention-days: forever
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RetentionAuditError, "invalid retention-days"):
                audit_workflow(path)

    def test_real_repository_report_is_bounded_and_nonempty(self):
        report = audit_repository(ROOT / ".github" / "workflows")
        self.assertGreater(report["workflow_count"], 0)
        self.assertGreater(report["artifact_upload_count"], 0)
        self.assertEqual(
            report["artifact_upload_count"],
            report["explicit_retention_count"] + report["implicit_retention_count"],
        )
        self.assertGreaterEqual(report["explicit_coverage_percent"], 0)
        self.assertLessEqual(report["explicit_coverage_percent"], 100)
        for upload in report["uploads"]:
            self.assertRegex(upload["workflow"], r"\.ya?ml$")
            self.assertGreater(upload["line"], 0)
            if upload["retention_state"] == "explicit":
                self.assertGreater(upload["retention_days"], 0)
            else:
                self.assertIsNone(upload["retention_days"])

    def test_cli_require_explicit_reports_gap_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "implicit.yml").write_text(
                """
jobs:
  proof:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          name: receipt
          path: receipt.json
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "zcloud_log_retention_audit.py"),
                    "--workflows",
                    str(root),
                    "--require-explicit",
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            {"ok": False, "error": "implicit_artifact_retention_present"},
            json.loads(result.stdout),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
