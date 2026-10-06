import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_progress_dimensions import (
    ProgressDimensionError,
    classify_milestone,
    project_dimensions,
    registry_dimensions,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))


class ProgressDimensionTests(unittest.TestCase):
    def test_explicit_dimension_is_canonical_even_if_title_is_ambiguous(self):
        dimension, basis = classify_milestone({
            "title": "Research validation deployment",
            "dimension": "research",
            "progress": 50,
        })
        self.assertEqual("research", dimension)
        self.assertEqual("explicit", basis)

    def test_ambiguous_keyword_fallback_refuses_to_guess(self):
        dimension, basis = classify_milestone({
            "title": "Beta deployment & real-user validation",
            "progress": 50,
        })
        self.assertIsNone(dimension)
        self.assertEqual("ambiguous", basis)

    def test_dimension_progress_is_only_mean_of_existing_evidence(self):
        project = {
            "id": "demo",
            "milestone_revision": "demo-v1",
            "progress_basis": "checkpoint evidence",
            "milestones": [
                {"title": "A", "dimension": "build", "progress": 25},
                {"title": "B", "dimension": "build", "progress": 75},
                {"title": "C", "dimension": "validation", "progress": 10},
            ],
        }
        report = project_dimensions(project)
        self.assertEqual(50.0, report["dimensions"]["build"]["progress"])
        self.assertEqual(10.0, report["dimensions"]["validation"]["progress"])
        self.assertNotIn("research", report["dimensions"])
        self.assertEqual("demo-v1", report["source_revision"])
        self.assertEqual("checkpoint evidence", report["progress_basis"])

    def test_done_flag_never_fabricates_missing_progress(self):
        report = project_dimensions({
            "id": "demo",
            "milestone_revision": "v1",
            "progress_basis": "evidence",
            "milestones": [
                {"title": "Core engine", "dimension": "build", "done": True},
                {"title": "Validation", "dimension": "validation", "progress": 40},
            ],
        })
        self.assertNotIn("build", report["dimensions"])
        self.assertEqual(1, report["counts"]["invalid_milestones"])
        self.assertEqual(40.0, report["dimensions"]["validation"]["progress"])

    def test_invalid_progress_and_invalid_explicit_dimension_fail_closed_per_milestone(self):
        report = project_dimensions({
            "id": "demo",
            "milestone_revision": "v1",
            "progress_basis": "evidence",
            "milestones": [
                {"title": "Core engine", "dimension": "build", "progress": True},
                {"title": "Research", "dimension": "strategy", "progress": 70},
                {"title": "Benchmark", "progress": float("nan")},
                {"title": "API", "progress": 120},
            ],
        })
        self.assertEqual({}, report["dimensions"])
        self.assertEqual("unavailable", report["status"])
        self.assertEqual(4, report["counts"]["invalid_milestones"])

    def test_current_ftmo_registry_has_all_four_evidence_dimensions(self):
        ftmo = next(project for project in PROJECTS if project["id"] == "ftmo")
        report = project_dimensions(ftmo)
        self.assertEqual(
            {"research", "build", "validation", "operations"},
            set(report["dimensions"]),
        )
        self.assertEqual(100.0, report["dimensions"]["research"]["progress"])
        self.assertEqual(100.0, report["dimensions"]["build"]["progress"])
        self.assertEqual(82.0, report["dimensions"]["validation"]["progress"])
        self.assertEqual(67.5, report["dimensions"]["operations"]["progress"])

    def test_registry_output_contains_no_milestone_titles(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "projects.json"
            secret_title = "sensitive-internal-title-never-emit"
            path.write_text(json.dumps([{
                "id": "demo",
                "milestone_revision": "v1",
                "progress_basis": "evidence",
                "milestones": [
                    {"title": secret_title, "dimension": "build", "progress": 50},
                ],
            }]), encoding="utf-8")
            report = registry_dimensions(path)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(secret_title, serialized)
        self.assertEqual(50.0, report["projects"][0]["dimensions"]["build"]["progress"])
        self.assertRegex(report["source_sha256"], r"^[0-9a-f]{64}$")

    def test_all_current_registry_dimensions_are_bounded_and_traceable(self):
        report = registry_dimensions(ROOT / "projects.json")
        self.assertEqual(len(PROJECTS), report["project_count"])
        self.assertGreater(report["available_count"], 0)
        for project in report["projects"]:
            with self.subTest(project=project["project_id"]):
                self.assertTrue(project["source_revision"])
                self.assertTrue(project["progress_basis"])
                for dimension, value in project["dimensions"].items():
                    self.assertIn(dimension, ("research", "build", "validation", "operations"))
                    self.assertGreaterEqual(value["progress"], 0)
                    self.assertLessEqual(value["progress"], 100)
                    self.assertGreater(value["milestone_count"], 0)
                    self.assertEqual(
                        "mean_of_existing_milestone_progress",
                        value["evidence"],
                    )

    def test_cli_supports_bounded_single_project_read(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "zcloud_progress_dimensions.py"),
                "--project",
                "ftmo",
                "--require-available",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("ftmo", payload["report"]["project"]["project_id"])
        self.assertNotIn("milestones", payload["report"]["project"])

    def test_unknown_project_and_invalid_registry_fail_closed(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "zcloud_progress_dimensions.py"),
                "--project",
                "not-real",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            {"ok": False, "error": "project_unknown"},
            json.loads(result.stdout),
        )
        with self.assertRaisesRegex(ProgressDimensionError, "projects_root_invalid"):
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "projects.json"
                path.write_text("{}", encoding="utf-8")
                registry_dimensions(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
