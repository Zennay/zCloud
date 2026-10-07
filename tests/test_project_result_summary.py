from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from scripts import zcloud_project_result_summary as summary


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_project_result_summary.py"


class ProjectResultSummaryTests(unittest.TestCase):
    def test_projects_latest_current_best_without_source_path_leakage(self):
        quality = {
            "available": True,
            "stage": "verifying",
            "comparison": {
                "latest": {
                    "label": "Nieuwste candidate",
                    "value": "Generation 21",
                    "unit": "",
                    "note": "Holdout pending",
                    "validated": False,
                    "source": "/opt/private/project/result.json",
                    "observed_at": "2026-10-07T00:00:00+00:00",
                },
                "current": {
                    "label": "Huidige live",
                    "value": "Generation 20",
                    "validated": True,
                    "source": "/opt/private/project/release.json",
                    "observed_at": "2026-10-06T23:00:00+00:00",
                },
                "best": {
                    "label": "Beste gevalideerd",
                    "value": "Generation 20",
                    "validated": True,
                    "source": "/opt/private/project/release.json",
                    "observed_at": "2026-10-06T23:00:00+00:00",
                },
            },
        }

        result = summary.project_summary("ftmo", quality)

        self.assertTrue(result["available"])
        self.assertEqual("project_adapter", result["source_mode"])
        self.assertEqual("available", result["evidence_state"])
        self.assertEqual("Nieuw resultaat wacht op validatie", result["plain_status"])
        self.assertEqual(3, result["result_count"])
        self.assertEqual("Generation 21", result["results"]["latest"]["value"])
        self.assertFalse(result["results"]["latest"]["validated"])
        self.assertTrue(result["results"]["best"]["evidence_present"])
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("/opt/private", encoded)
        self.assertNotIn('"source"', encoded)

    def test_missing_adapter_data_is_explicit_generic_only_when_unregistered(self):
        result = summary.project_summary(
            "supa",
            {
                "available": False,
                "headline": None,
                "comparison": {"available": False, "latest": None, "current": None, "best": None},
            },
            adapter_backed=False,
        )

        self.assertFalse(result["available"])
        self.assertEqual("generic_only", result["source_mode"])
        self.assertEqual("missing", result["evidence_state"])
        self.assertEqual("Geen projectspecifieke resultaatdata", result["plain_status"])
        self.assertEqual({}, result["results"])

    def test_registered_adapter_with_missing_evidence_remains_project_adapter(self):
        result = summary.build_report(
            ["ftmo"],
            quality_reader=lambda _project_id: {
                "available": False,
                "comparison": {"available": False, "latest": None, "current": None, "best": None},
            },
            adapter_projects_reader=lambda: ("ftmo",),
        )

        project = result["projects"][0]
        self.assertEqual("project_adapter", project["source_mode"])
        self.assertEqual("missing", project["evidence_state"])
        self.assertFalse(project["available"])

    def test_plain_status_marks_current_best_as_validated(self):
        result = summary.project_summary(
            "haxlab",
            {
                "comparison": {
                    "current": {"value": "champion-a", "validated": True},
                    "best": {"value": "champion-a", "validated": True},
                }
            },
            adapter_backed=True,
        )

        self.assertEqual("Huidig resultaat is gevalideerd", result["plain_status"])

    def test_non_scalar_result_value_is_omitted_fail_closed(self):
        result = summary.project_summary(
            "haxlab",
            {
                "available": True,
                "comparison": {
                    "latest": {"value": {"nested": "payload"}},
                    "current": {"value": ["unexpected"]},
                    "best": {"value": float("nan")},
                },
            },
        )

        self.assertFalse(result["available"])
        self.assertEqual({}, result["results"])

    def test_build_report_deduplicates_project_ids_and_preserves_order(self):
        qualities = {
            "ftmo": {"available": True, "comparison": {"latest": {"value": "g21"}}},
            "haxlab": {"available": True, "comparison": {"current": {"value": "champion-a"}}},
        }

        result = summary.build_report(
            ["ftmo", "haxlab", "ftmo"],
            quality_reader=lambda project_id: qualities[project_id],
            adapter_projects_reader=lambda: ("ftmo", "haxlab"),
        )

        self.assertEqual(["ftmo", "haxlab"], [item["project_id"] for item in result["projects"]])
        self.assertEqual(2, result["project_count"])
        self.assertEqual(2, result["available_count"])

    def test_invalid_project_id_is_rejected(self):
        with self.assertRaisesRegex(summary.ProjectResultSummaryError, "invalid project id"):
            summary.build_report(
                ["../secret"],
                quality_reader=lambda _project_id: {},
                adapter_projects_reader=lambda: (),
            )

    def test_oversize_project_id_is_rejected_instead_of_truncated(self):
        with self.assertRaisesRegex(summary.ProjectResultSummaryError, "invalid project id"):
            summary.build_report(
                ["a" * 33],
                quality_reader=lambda _project_id: {},
                adapter_projects_reader=lambda: (),
            )

    def test_project_limit_is_bounded(self):
        with self.assertRaisesRegex(summary.ProjectResultSummaryError, "at most 32"):
            summary.build_report(
                [f"p{i}" for i in range(33)],
                quality_reader=lambda _project_id: {},
                adapter_projects_reader=lambda: (),
            )

    def test_cli_generic_project_is_valid_json_and_non_mutating(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", "supa"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(1, payload["project_count"])
        self.assertEqual("supa", payload["projects"][0]["project_id"])
        self.assertEqual("generic_only", payload["projects"][0]["source_mode"])

    def test_require_data_returns_three_for_missing_comparison(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", "supa", "--require-data"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(0, payload["available_count"])
        self.assertEqual(3, completed.returncode)


if __name__ == "__main__":
    unittest.main()
