import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import zcloud_notion_handoff_freshness as freshness


NOW = datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)


class NotionHandoffFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-notion-handoff-")
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects.json"
        self.source = self.root / "handoffs.json"
        self.projects.write_text(
            json.dumps([
                {"id": "cloud", "status": "active"},
                {"id": "ftmo", "status": "active"},
                {"id": "old", "status": "archived"},
            ]),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write_source(self, rows, captured_at=None):
        if captured_at is None:
            captured_at = NOW.isoformat()
        self.source.write_text(
            json.dumps({
                "schema_version": freshness.SOURCE_SCHEMA_VERSION,
                "captured_at": captured_at,
                "handoffs": rows,
            }),
            encoding="utf-8",
        )

    @staticmethod
    def row(project, *, minutes=5, source_kind=freshness.ALLOWED_SOURCE_KIND):
        at = NOW - timedelta(minutes=minutes)
        return {
            "project_id": project,
            "source_kind": source_kind,
            "handoff_at": at.isoformat(),
            "recorded_at": (at + timedelta(seconds=30)).isoformat(),
        }

    def test_fresh_stale_and_missing_are_evidence_based(self):
        self.write_source([
            self.row("cloud", minutes=5),
            self.row("ftmo", minutes=180),
        ])
        payload = freshness.build_report(
            self.projects, self.source, stale_minutes=60, now=NOW
        )
        by_id = {item["project_id"]: item for item in payload["projects"]}
        self.assertEqual("fresh", by_id["cloud"]["status"])
        self.assertEqual("stale", by_id["ftmo"]["status"])
        self.assertFalse(payload["ready"])

        self.write_source([self.row("cloud")])
        payload = freshness.build_report(
            self.projects, self.source, stale_minutes=60, now=NOW
        )
        by_id = {item["project_id"]: item for item in payload["projects"]}
        self.assertEqual("missing", by_id["ftmo"]["status"])
        self.assertEqual("durable_handoff_event_missing", by_id["ftmo"]["reason"])

    def test_page_last_edited_proxy_is_explicitly_forbidden(self):
        self.write_source([
            self.row("cloud", source_kind="notion_page_last_edited"),
            self.row("ftmo"),
        ])
        payload = freshness.build_report(self.projects, self.source, now=NOW)
        cloud = next(item for item in payload["projects"] if item["project_id"] == "cloud")
        self.assertEqual("invalid", cloud["status"])
        self.assertEqual("page_age_proxy_forbidden", cloud["reason"])
        self.assertFalse(payload["source_contract"]["page_last_edited_is_handoff_evidence"])

    def test_unknown_source_kind_never_becomes_fresh(self):
        self.write_source([
            self.row("cloud", source_kind="manual_guess"),
            self.row("ftmo"),
        ])
        payload = freshness.build_report(self.projects, self.source, now=NOW)
        cloud = next(item for item in payload["projects"] if item["project_id"] == "cloud")
        self.assertEqual("invalid", cloud["status"])
        self.assertEqual("unsupported_source_kind", cloud["reason"])

    def test_duplicate_unknown_and_archived_rows_fail_closed(self):
        self.write_source([self.row("cloud"), self.row("cloud")])
        with self.assertRaisesRegex(ValueError, "duplicate handoff project id"):
            freshness.build_report(self.projects, self.source, now=NOW)

        self.write_source([self.row("old")])
        with self.assertRaisesRegex(ValueError, "unknown or archived"):
            freshness.build_report(self.projects, self.source, now=NOW)

        self.write_source([self.row("ghost")])
        with self.assertRaisesRegex(ValueError, "unknown or archived"):
            freshness.build_report(self.projects, self.source, now=NOW)

    def test_invalid_temporal_order_and_future_values_fail_closed(self):
        row = self.row("cloud")
        row["handoff_at"] = NOW.isoformat()
        row["recorded_at"] = (NOW - timedelta(minutes=1)).isoformat()
        self.write_source([row])
        with self.assertRaisesRegex(ValueError, "handoff_at must not be after recorded_at"):
            freshness.build_report(self.projects, self.source, now=NOW)

        row = self.row("cloud")
        self.write_source([row], captured_at=(NOW + timedelta(minutes=10)).isoformat())
        with self.assertRaisesRegex(ValueError, "captured_at timestamp is implausibly"):
            freshness.build_report(self.projects, self.source, now=NOW)

    def test_output_is_bounded_and_does_not_echo_source_payload(self):
        self.write_source([self.row("cloud"), self.row("ftmo")])
        payload = freshness.build_report(self.projects, self.source, now=NOW)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("handoff_at", serialized)
        self.assertNotIn("recorded_at", serialized)
        self.assertNotIn("captured_at\":", serialized)
        self.assertNotIn("source_kind\":", serialized)

    def test_symlink_inputs_and_unsupported_fields_are_rejected(self):
        self.write_source([self.row("cloud"), self.row("ftmo")])
        link = self.root / "source-link.json"
        try:
            link.symlink_to(self.source)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            freshness.build_report(self.projects, link, now=NOW)

        bad = self.row("cloud")
        bad["page_last_edited_at"] = NOW.isoformat()
        self.write_source([bad])
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            freshness.build_report(self.projects, self.source, now=NOW)

    def test_threshold_is_bounded(self):
        self.write_source([self.row("cloud"), self.row("ftmo")])
        for value in (0, freshness.MAX_STALE_MINUTES + 1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "stale_minutes"):
                    freshness.build_report(
                        self.projects, self.source, stale_minutes=value, now=NOW
                    )

    def test_cli_require_fresh_exits_nonzero_on_missing_handoff(self):
        self.write_source([self.row("cloud")])
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "zcloud_notion_handoff_freshness.py"),
                "--projects",
                str(self.projects),
                "--source",
                str(self.source),
                "--now",
                NOW.isoformat(),
                "--require-fresh",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(3, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual(1, payload["counts"]["missing"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
