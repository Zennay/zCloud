from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_adapter_coverage_report as coverage


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_adapter_coverage_report.py"


class AdapterCoverageReportTests(unittest.TestCase):
    def test_active_projects_are_split_between_adapter_and_generic_fallback(self):
        registry = [
            {"id": "ftmo", "name": "FTMO", "status": "active", "repo": "/private/ftmo"},
            {"id": "supa", "name": "SUPA", "status": "active", "repo": "/private/supa"},
            {"id": "old", "name": "Old", "status": "archived"},
        ]

        result = coverage.build_report(
            registry,
            adapter_projects_reader=lambda: ("ftmo",),
        )

        self.assertEqual(2, result["active_project_count"])
        self.assertEqual(1, result["adapter_backed_count"])
        self.assertEqual(1, result["generic_only_count"])
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(["supa"], result["generic_only_projects"])
        self.assertEqual(
            ["project_adapter", "generic_only"],
            [item["result_mode"] for item in result["projects"]],
        )
        self.assertNotIn("/private", json.dumps(result, sort_keys=True))

    def test_complete_coverage_has_no_missing_projects(self):
        result = coverage.build_report(
            [
                {"id": "ftmo", "status": "active"},
                {"id": "haxlab", "status": "active"},
            ],
            adapter_projects_reader=lambda: ("haxlab", "ftmo"),
        )

        self.assertTrue(result["coverage_complete"])
        self.assertEqual(2, result["adapter_backed_count"])
        self.assertEqual([], result["generic_only_projects"])

    def test_duplicate_active_project_id_fails_closed(self):
        with self.assertRaisesRegex(coverage.AdapterCoverageError, "duplicate active project id"):
            coverage.build_report(
                [
                    {"id": "ftmo", "status": "active"},
                    {"id": "FTMO", "status": "active"},
                ],
                adapter_projects_reader=lambda: (),
            )

    def test_invalid_project_id_fails_closed(self):
        with self.assertRaisesRegex(coverage.AdapterCoverageError, "invalid project id"):
            coverage.build_report(
                [{"id": "../secret", "status": "active"}],
                adapter_projects_reader=lambda: (),
            )

    def test_registry_must_be_json_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            path.write_text('{"id":"ftmo"}', encoding="utf-8")
            with self.assertRaisesRegex(coverage.AdapterCoverageError, "JSON list"):
                coverage.load_registry(path)

    def test_registry_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.json"
            link = root / "projects.json"
            real.write_text("[]", encoding="utf-8")
            link.symlink_to(real)
            with self.assertRaisesRegex(coverage.AdapterCoverageError, "non-symlink"):
                coverage.load_registry(link)

    def test_cli_reports_current_registry_without_paths(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(completed.stdout)

        self.assertGreaterEqual(payload["active_project_count"], 1)
        self.assertEqual(
            payload["active_project_count"],
            payload["adapter_backed_count"] + payload["generic_only_count"],
        )
        self.assertNotIn("/opt/", completed.stdout)
        self.assertNotIn("notion_url", completed.stdout)
        self.assertNotIn("repo_url", completed.stdout)

    def test_require_complete_is_a_non_mutating_gate(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--require-complete"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout)

        expected = 0 if payload["coverage_complete"] else 3
        self.assertEqual(expected, completed.returncode)


if __name__ == "__main__":
    unittest.main()
