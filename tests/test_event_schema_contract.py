import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_event_schema_validate.py"
SCHEMA = ROOT / "schemas" / "control-plane-event-v1.json"
SPEC = importlib.util.spec_from_file_location("zcloud_event_schema_validate", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class EventSchemaValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event TEXT,
                    target TEXT,
                    title TEXT,
                    generating INTEGER,
                    sending INTEGER,
                    reason TEXT,
                    tab_id INTEGER,
                    error TEXT,
                    project_id TEXT,
                    progress_at TEXT,
                    assistant_chars INTEGER,
                    worker_slot INTEGER NOT NULL DEFAULT 1
                )
                """
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert_event(
        self,
        event="heartbeat",
        *,
        ts=None,
        target="secret-target-url",
        title="secret-title",
        reason="secret-reason",
        error="secret-error",
        generating=0,
        sending=0,
        worker_slot=1,
        assistant_chars=42,
        progress_at=None,
    ):
        ts = ts if ts is not None else datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO runner_events(
                    ts,event,target,title,generating,sending,reason,tab_id,error,
                    project_id,progress_at,assistant_chars,worker_slot
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    event,
                    target,
                    title,
                    generating,
                    sending,
                    reason,
                    991,
                    error,
                    "cloud",
                    progress_at,
                    assistant_chars,
                    worker_slot,
                ),
            )

    def test_repository_schema_contract_is_valid(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual([], VALIDATOR.validate_contract(schema))
        self.assertEqual(1, schema["schema_version"])
        self.assertFalse(schema["storage"]["allow_unclassified_columns"])
        self.assertEqual(
            ["safe_structured", "internal_key"],
            schema["privacy"]["canonical_projection_classes"],
        )
        self.assertTrue(schema["event_type_contract"]["allow_unclassified_types"])

    def test_live_shape_and_core_event_are_green(self):
        self.insert_event("heartbeat")
        self.insert_event("generation-finished")

        report = VALIDATOR.validate(SCHEMA, self.db)

        self.assertTrue(report["ok"], report)
        self.assertEqual(2, report["storage"]["data_quality"]["total_rows"])
        self.assertEqual([], report["storage"]["missing_required_columns"])
        self.assertGreaterEqual(
            report["storage"]["event_types"]["classification_coverage_pct"], 100.0
        )

    def test_canonical_projection_excludes_sensitive_free_text(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        row = {
            "id": 7,
            "ts": "2026-10-06T10:00:00Z",
            "event": "heartbeat",
            "target": "https://private.invalid/token",
            "title": "PRIVATE TITLE",
            "generating": 1,
            "sending": 0,
            "reason": "PRIVATE REASON",
            "tab_id": 99,
            "error": "PRIVATE ERROR",
            "project_id": "cloud",
            "progress_at": "2026-10-06T09:59:00+00:00",
            "assistant_chars": 123,
            "worker_slot": 2,
        }

        projected = VALIDATOR.project_event_row(row, schema)

        self.assertEqual(
            {
                "event_id",
                "observed_at",
                "event_type",
                "project_id",
                "worker_slot",
                "generating",
                "sending",
                "progress_at",
                "assistant_chars",
            },
            set(projected),
        )
        encoded = json.dumps(projected, sort_keys=True)
        for secret in (
            "private.invalid",
            "PRIVATE TITLE",
            "PRIVATE REASON",
            "PRIVATE ERROR",
        ):
            self.assertNotIn(secret, encoded)
        self.assertEqual("2026-10-06T10:00:00+00:00", projected["observed_at"])

    def test_canonical_projection_fails_closed_on_invalid_state(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        row = {
            "id": 1,
            "ts": "2026-10-06T10:00:00",
            "event": "bad_event",
            "project_id": "cloud",
            "worker_slot": 0,
            "generating": 3,
            "sending": 0,
            "progress_at": None,
            "assistant_chars": -1,
        }

        with self.assertRaises(VALIDATOR.EventSchemaError):
            VALIDATOR.project_event_row(row, schema)

    def test_unknown_event_type_is_reported_without_breaking_contract(self):
        self.insert_event("future-safe-event")

        report = VALIDATOR.validate(SCHEMA, self.db)

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            ["future-safe-event"],
            report["storage"]["event_types"]["unclassified_types"],
        )
        self.assertEqual(0, report["storage"]["event_types"]["classified"])

    def test_malformed_rows_fail_data_quality(self):
        self.insert_event("", ts="not-a-time", generating=7, sending=-1, worker_slot=0)
        self.insert_event(
            "heartbeat",
            progress_at="not-a-time",
            assistant_chars=-10,
        )

        report = VALIDATOR.validate(SCHEMA, self.db)

        self.assertFalse(report["ok"])
        quality = report["storage"]["data_quality"]
        self.assertEqual(1, quality["invalid_ts"])
        self.assertEqual(1, quality["blank_event"])
        self.assertEqual(1, quality["invalid_generating"])
        self.assertEqual(1, quality["invalid_sending"])
        self.assertEqual(1, quality["invalid_worker_slot"])
        self.assertEqual(1, quality["invalid_assistant_chars"])
        self.assertEqual(1, quality["invalid_progress_at"])

    def test_unclassified_storage_column_fails_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("ALTER TABLE runner_events ADD COLUMN future_payload TEXT")
        self.insert_event()

        report = VALIDATOR.validate(SCHEMA, self.db)

        self.assertFalse(report["ok"])
        self.assertEqual(
            ["future_payload"], report["storage"]["unclassified_columns"]
        )

    def test_sensitive_field_values_never_appear_in_report(self):
        self.insert_event(
            target="https://chat.example.invalid/private-token",
            title="PRIVATE TITLE",
            reason="PRIVATE REASON",
            error="PRIVATE ERROR",
        )

        report = VALIDATOR.validate(SCHEMA, self.db)
        encoded = json.dumps(report, sort_keys=True)

        self.assertTrue(report["ok"], report)
        for secret in (
            "private-token",
            "PRIVATE TITLE",
            "PRIVATE REASON",
            "PRIVATE ERROR",
        ):
            self.assertNotIn(secret, encoded)
        self.assertEqual(
            ["target", "title", "tab_id", "error"],
            report["privacy"]["never_export_fields"],
        )

    def test_validation_does_not_modify_database_bytes_or_mtime(self):
        self.insert_event()
        before_bytes = self.db.read_bytes()
        before_stat = self.db.stat()

        report = VALIDATOR.validate(SCHEMA, self.db)

        after_stat = self.db.stat()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["storage"]["query_only"])
        self.assertEqual(0, report["storage"]["connection_total_changes"])
        self.assertEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlink_database_is_rejected(self):
        linked = Path(self.tmp.name) / "linked.db"
        linked.symlink_to(self.db)

        with self.assertRaises(VALIDATOR.EventSchemaError):
            VALIDATOR.validate(SCHEMA, linked)

    def test_cli_is_machine_readable_and_secret_safe(self):
        self.insert_event()

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--schema", str(SCHEMA), "--db", str(self.db)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertNotIn("secret-target-url", proc.stdout)
        self.assertNotIn("secret-title", proc.stdout)
        self.assertNotIn("secret-reason", proc.stdout)
        self.assertNotIn("secret-error", proc.stdout)

    def test_contract_rejects_non_object_privacy_without_crashing(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema["privacy"] = "invalid"

        errors = VALIDATOR.validate_contract(schema)

        self.assertIn("privacy must be an object", errors)
        self.assertIn("canonical_projection_classes must be non-empty", errors)

    def test_contract_rejects_forbidden_projection_privacy(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema["canonical_projection"]["fields"]["unsafe"] = {"source": "reason"}

        errors = VALIDATOR.validate_contract(schema)

        self.assertIn(
            "canonical field unsafe may not project restricted_text source reason",
            errors,
        )

    def test_contract_requires_boolean_unclassified_policy(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema["event_type_contract"]["allow_unclassified_types"] = "yes"

        errors = VALIDATOR.validate_contract(schema)

        self.assertIn(
            "event_type_contract.allow_unclassified_types must be boolean",
            errors,
        )

    def test_contract_rejects_duplicate_family_assignment(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema["event_type_contract"]["families"]["other"] = ["heartbeat"]

        errors = VALIDATOR.validate_contract(schema)

        self.assertTrue(
            any("heartbeat belongs to both" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
