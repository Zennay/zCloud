from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts import zcloud_project_telemetry_freshness as freshness


NOW = datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)


class ProjectTelemetryFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.projects = self.root / "projects.json"
        self.projects.write_text(
            json.dumps(
                [
                    {"id": "ftmo", "status": "active"},
                    {"id": "haxlab", "status": "active"},
                    {"id": "ulab", "status": "active"},
                    {"id": "supa", "status": "active"},
                    {"id": "archived", "status": "archived"},
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _point(value: object, observed_at: str, source: str = "/private/source") -> dict:
        return {
            "label": "metric",
            "value": value,
            "source": source,
            "observed_at": observed_at,
        }

    def _build(self, snapshots: dict[str, dict], adapters=("ftmo", "haxlab", "ulab")) -> dict:
        return freshness.build_report(
            self.projects,
            stale_minutes=60,
            now=NOW,
            quality_reader=lambda project_id: snapshots[project_id],
            adapter_projects_reader=lambda: adapters,
        )

    def test_classifies_fresh_stale_missing_and_generic_fallback(self) -> None:
        snapshots = {
            "ftmo": {
                "available": True,
                "headline": self._point(52.1, "2026-10-06T10:40:00+00:00"),
                "items": [],
                "comparison": {},
            },
            "haxlab": {
                "available": True,
                "headline": self._point(72.9, "2026-10-06T08:00:00+00:00"),
                "items": [],
                "comparison": {},
            },
            "ulab": {
                "available": False,
                "headline": None,
                "items": [],
                "comparison": {},
            },
        }

        payload = self._build(snapshots)
        by_id = {item["project_id"]: item for item in payload["projects"]}

        self.assertEqual("fresh", by_id["ftmo"]["status"])
        self.assertFalse(by_id["ftmo"]["warning"])
        self.assertEqual("stale", by_id["haxlab"]["status"])
        self.assertTrue(by_id["haxlab"]["warning"])
        self.assertEqual("missing", by_id["ulab"]["status"])
        self.assertTrue(by_id["ulab"]["warning"])
        self.assertEqual("generic_only", by_id["supa"]["status"])
        self.assertFalse(by_id["supa"]["warning"])
        self.assertNotIn("archived", by_id)
        self.assertEqual(2, payload["warning_count"])

    def test_missing_provenance_is_incomplete_and_values_are_not_emitted(self) -> None:
        secret_value = "secret-model-output"
        snapshots = {
            "ftmo": {
                "available": True,
                "headline": {
                    "label": "candidate",
                    "value": secret_value,
                    "observed_at": "2026-10-06T10:55:00+00:00",
                },
                "items": [],
                "comparison": {},
            }
        }
        payload = freshness.build_report(
            self.projects,
            project_ids=["ftmo"],
            stale_minutes=60,
            now=NOW,
            quality_reader=lambda project_id: snapshots[project_id],
            adapter_projects_reader=lambda: ("ftmo",),
        )

        item = payload["projects"][0]
        self.assertEqual("incomplete", item["status"])
        self.assertEqual(1, item["missing_provenance_points"])
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(secret_value, serialized)
        self.assertNotIn("/private/source", serialized)
        self.assertNotIn("headline", serialized)

    def test_mixed_point_ages_use_freshest_point_for_snapshot_freshness(self) -> None:
        snapshots = {
            "ftmo": {
                "available": True,
                "headline": self._point(1, "2026-10-06T08:00:00+00:00"),
                "items": [self._point(2, "2026-10-06T10:50:00+00:00")],
                "comparison": {},
            }
        }
        payload = freshness.build_report(
            self.projects,
            project_ids=["ftmo"],
            stale_minutes=60,
            now=NOW,
            quality_reader=lambda project_id: snapshots[project_id],
            adapter_projects_reader=lambda: ("ftmo",),
        )
        item = payload["projects"][0]
        self.assertEqual("fresh", item["status"])
        self.assertEqual(600, item["newest_evidence_age_seconds"])
        self.assertEqual(10800, item["oldest_evidence_age_seconds"])

    def test_invalid_relevant_timestamp_fails_closed(self) -> None:
        snapshots = {
            "ftmo": {
                "available": True,
                "headline": self._point(1, "not-a-time"),
                "items": [],
                "comparison": {},
            }
        }
        with self.assertRaisesRegex(ValueError, "timestamp invalid"):
            freshness.build_report(
                self.projects,
                project_ids=["ftmo"],
                now=NOW,
                quality_reader=lambda project_id: snapshots[project_id],
                adapter_projects_reader=lambda: ("ftmo",),
            )

    def test_implausible_future_timestamp_fails_closed(self) -> None:
        snapshots = {
            "ftmo": {
                "available": True,
                "headline": self._point(1, "2026-10-06T12:00:00+00:00"),
                "items": [],
                "comparison": {},
            }
        }
        with self.assertRaisesRegex(ValueError, "implausibly in the future"):
            freshness.build_report(
                self.projects,
                project_ids=["ftmo"],
                now=NOW,
                quality_reader=lambda project_id: snapshots[project_id],
                adapter_projects_reader=lambda: ("ftmo",),
            )

    def test_project_filter_is_bounded_and_deduplicated(self) -> None:
        payload = freshness.build_report(
            self.projects,
            project_ids=["supa", "supa"],
            now=NOW,
            quality_reader=lambda project_id: {},
            adapter_projects_reader=lambda: (),
        )
        self.assertEqual(["supa"], [item["project_id"] for item in payload["projects"]])

        with self.assertRaisesRegex(ValueError, "unknown or archived project"):
            freshness.build_report(
                self.projects,
                project_ids=["archived"],
                now=NOW,
                quality_reader=lambda project_id: {},
                adapter_projects_reader=lambda: (),
            )

    def test_symlink_projects_file_is_rejected(self) -> None:
        link = self.root / "projects-link.json"
        try:
            link.symlink_to(self.projects)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            freshness.build_report(
                link,
                now=NOW,
                quality_reader=lambda project_id: {},
                adapter_projects_reader=lambda: (),
            )

    def test_threshold_is_bounded(self) -> None:
        for value in (0, 10081):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "stale_minutes"):
                    freshness.build_report(
                        self.projects,
                        stale_minutes=value,
                        now=NOW,
                        quality_reader=lambda project_id: {},
                        adapter_projects_reader=lambda: (),
                    )


if __name__ == "__main__":
    unittest.main()
