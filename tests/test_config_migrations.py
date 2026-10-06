import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zcloud_config_migrate.py"
SPEC = importlib.util.spec_from_file_location("zcloud_config_migrate", MODULE_PATH)
migrate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migrate)

FIXTURES = ROOT / "tests" / "fixtures" / "config-migrations"


class ConfigMigrationTests(unittest.TestCase):
    def test_registry_covers_every_canonical_config(self):
        registry = migrate.load_registry(ROOT / "config-schema-versions.json")
        self.assertEqual(set(migrate.CANONICAL_CONFIGS), set(registry["configs"]))
        self.assertEqual("embedded", registry["configs"]["project-contracts.json"]["version_source"])
        self.assertEqual(
            "schema_version",
            registry["configs"]["project-contracts.json"]["version_field"],
        )
        for name in ("projects.json", "project-layout.json", "resource-policy.json"):
            self.assertEqual("sidecar", registry["configs"][name]["version_source"])

    def test_current_repository_matches_version_registry(self):
        result = migrate.validate_current(ROOT, ROOT / "config-schema-versions.json")
        self.assertTrue(result["ok"])
        self.assertEqual(
            {
                "projects.json": 1,
                "project-layout.json": 1,
                "resource-policy.json": 1,
                "project-contracts.json": 1,
            },
            result["versions"],
        )

    def test_project_contracts_v0_fixture_migrates_exactly_to_v1(self):
        source = json.loads(
            (FIXTURES / "project-contracts-v0.json").read_text(encoding="utf-8")
        )
        expected = json.loads(
            (FIXTURES / "project-contracts-v1.json").read_text(encoding="utf-8")
        )
        result = migrate.migrate_document("project-contracts.json", source, 0, 1)
        self.assertEqual(expected, result)
        self.assertNotIn("schema_version", source)

    def test_missing_or_future_embedded_version_fails_closed(self):
        registry = migrate.load_registry(ROOT / "config-schema-versions.json")
        spec = registry["configs"]["project-contracts.json"]
        with self.assertRaisesRegex(migrate.ConfigMigrationError, "must be an integer"):
            migrate.current_document_version("project-contracts.json", {}, spec)
        with self.assertRaisesRegex(migrate.ConfigMigrationError, "schema version mismatch"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for name in migrate.CANONICAL_CONFIGS:
                    source = ROOT / name
                    target = root / name
                    target.write_bytes(source.read_bytes())
                contracts = json.loads(
                    (root / "project-contracts.json").read_text(encoding="utf-8")
                )
                contracts["schema_version"] = 2
                (root / "project-contracts.json").write_text(
                    json.dumps(contracts) + "\n", encoding="utf-8"
                )
                migrate.validate_current(root, ROOT / "config-schema-versions.json")

    def test_migration_plan_is_forward_only_and_requires_every_edge(self):
        self.assertEqual(
            [(0, 1)],
            migrate.migration_plan("project-contracts.json", 0, 1),
        )
        with self.assertRaisesRegex(migrate.ConfigMigrationError, "downgrade"):
            migrate.migration_plan("project-contracts.json", 1, 0)
        with self.assertRaisesRegex(migrate.ConfigMigrationError, "missing migration edge 1->2"):
            migrate.migration_plan("project-contracts.json", 0, 2)
        with self.assertRaisesRegex(migrate.ConfigMigrationError, "missing migration edge 0->1"):
            migrate.migration_plan("projects.json", 0, 1)

    def test_cli_refuses_in_place_migration(self):
        source = FIXTURES / "project-contracts-v0.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "migrate",
                "--config",
                "project-contracts.json",
                "--from-version",
                "0",
                "--to-version",
                "1",
                "--input",
                str(source),
                "--output",
                str(source),
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, proc.returncode)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("refusing in-place", payload["error"])

    def test_cli_writes_deterministic_non_destructive_migration(self):
        source = FIXTURES / "project-contracts-v0.json"
        expected = json.loads(
            (FIXTURES / "project-contracts-v1.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "project-contracts-v1.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "migrate",
                    "--config",
                    "project-contracts.json",
                    "--from-version",
                    "0",
                    "--to-version",
                    "1",
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(expected, json.loads(output.read_text(encoding="utf-8")))
            self.assertNotIn(
                "schema_version",
                json.loads(source.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
