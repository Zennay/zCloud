import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_migration_safety_guard",
    ROOT / "scripts" / "zcloud_migration_safety_guard.py",
)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(guard)


def change(path, patch, status="M"):
    return guard.Change(status=status, path=path, patch=patch)


VALID_RECORD = """# Migration record

## Scope
Change the zCloud runtime storage contract for one bounded capability.

## Forward
Apply the new schema/config shape after the pre-change validation gate is green.

## Rollback
Restore the previous schema/config snapshot and restart only after readback succeeds.

## Validation
Run focused migration tests, full regression, config validation and post-change readback.
"""


class MigrationSafetyGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "migrations").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write_record(self, name="20261006-example.md", text=VALID_RECORD):
        path = self.root / "docs" / "migrations" / name
        path.write_text(text, encoding="utf-8")
        return f"docs/migrations/{name}"

    def test_non_migration_change_passes_without_record(self):
        result = guard.assess(
            [change("scripts/report.py", "@@\n+print('read only')")],
            self.root,
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["migration_risk"])

    def test_guard_implementation_does_not_self_classify_as_migration(self):
        result = guard.assess(
            [change("scripts/zcloud_migration_safety_guard.py", "@@\n+new safety check")],
            self.root,
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["migration_risk"])

    def test_sql_ddl_requires_migration_record(self):
        result = guard.assess(
            [change("server.py", "@@\n+conn.execute('ALTER TABLE runner_events ADD COLUMN trace_id TEXT')")],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertIn("alter_table", result["risky_changes"][0]["signals"])

    def test_create_table_is_also_schema_migration(self):
        result = guard.assess(
            [change("project_runtime.py", "@@\n+CREATE TABLE IF NOT EXISTS durable_links(id INTEGER)")],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertIn("create_table", result["risky_changes"][0]["signals"])

    def test_migration_named_script_requires_record(self):
        result = guard.assess(
            [change("scripts/zcloud_config_migrate.py", "@@\n+def migrate(data): return data")],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertIn("migration_path", result["risky_changes"][0]["signals"])

    def test_config_schema_version_change_requires_record(self):
        result = guard.assess(
            [change("project-contracts.json", '@@\n-  "schema_version": 1,\n+  "schema_version": 2,')],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertIn("config_schema_version", result["risky_changes"][0]["signals"])

    def test_version_registry_change_requires_record(self):
        result = guard.assess(
            [change("config-schema-versions.json", '@@\n+{"projects.json": 2}')],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertIn("config_version_registry", result["risky_changes"][0]["signals"])

    def test_valid_record_satisfies_risky_change(self):
        record = self.write_record()
        result = guard.assess(
            [
                change("server.py", "@@\n+ALTER TABLE runner_events ADD COLUMN trace_id TEXT"),
                change(record, "@@\n+# Migration record", status="A"),
            ],
            self.root,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["migration_risk"])

    def test_record_requires_forward_rollback_scope_and_validation(self):
        record = self.write_record(
            text="""# Incomplete

## Scope
This migration changes one durable state contract in the control plane.

## Forward
Apply the new shape after validation and preserve the old snapshot.

## Validation
Run the migration tests and inspect exact readback before promotion.
"""
        )
        result = guard.assess(
            [
                change("server.py", "@@\n+DROP TABLE old_state"),
                change(record, "@@\n+# Incomplete", status="A"),
            ],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("missing ## Rollback" in e for e in result["errors"]))

    def test_placeholder_rollback_is_rejected(self):
        record = self.write_record(
            text=VALID_RECORD.replace(
                "Restore the previous schema/config snapshot and restart only after readback succeeds.",
                "N/A",
            )
        )
        result = guard.assess(
            [
                change("server.py", "@@\n+ALTER TABLE state ADD COLUMN x TEXT"),
                change(record, "@@\n+# Migration record", status="A"),
            ],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("Rollback cannot be a placeholder" in e for e in result["errors"]))

    def test_docs_and_tests_do_not_create_false_migration_risk(self):
        result = guard.assess(
            [
                change("docs/runtime-control-plane.md", "@@\n+ALTER TABLE example only"),
                change("tests/test_schema.py", "@@\n+sql = 'CREATE TABLE sample(id)'"),
            ],
            self.root,
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["migration_risk"])

    def test_symlink_record_fails_closed(self):
        real = self.root / "real.md"
        real.write_text(VALID_RECORD, encoding="utf-8")
        record = self.root / "docs" / "migrations" / "20261006-link.md"
        record.symlink_to(real)
        result = guard.assess(
            [
                change("server.py", "@@\n+ALTER TABLE state ADD COLUMN x TEXT"),
                change("docs/migrations/20261006-link.md", "@@\n+# link", status="A"),
            ],
            self.root,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("symlink migration record refused" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
