import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zcloud_config_migrate.py"
SPEC = importlib.util.spec_from_file_location("zcloud_config_migrate_roundtrip", MODULE_PATH)
migrate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migrate)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConfigRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.registry = migrate.load_registry(ROOT / "config-schema-versions.json")

    def test_every_canonical_config_json_round_trips_semantically(self):
        for name in migrate.CANONICAL_CONFIGS:
            with self.subTest(config=name):
                source = ROOT / name
                before = json.loads(source.read_text(encoding="utf-8"))
                rendered = json.dumps(
                    before,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n"
                after = json.loads(rendered)
                self.assertEqual(before, after)

    def test_current_version_identity_migration_is_deep_copy(self):
        for name in migrate.CANONICAL_CONFIGS:
            with self.subTest(config=name):
                source = json.loads((ROOT / name).read_text(encoding="utf-8"))
                version = int(self.registry["configs"][name]["current_version"])
                result = migrate.migrate_document(name, source, version, version)
                self.assertEqual(source, result)
                self.assertIsNot(source, result)

                if isinstance(result, dict):
                    result["__roundtrip_probe__"] = True
                    self.assertNotIn("__roundtrip_probe__", source)
                elif isinstance(result, list):
                    result.append({"__roundtrip_probe__": True})
                    self.assertNotEqual(source, result)

    def test_explicit_output_round_trip_preserves_source_and_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            out_root = Path(td)
            for name in migrate.CANONICAL_CONFIGS:
                with self.subTest(config=name):
                    source = ROOT / name
                    before_hash = sha256(source)
                    before = json.loads(source.read_text(encoding="utf-8"))
                    version = int(self.registry["configs"][name]["current_version"])
                    output = out_root / name

                    result = migrate.write_migrated(
                        input_path=source,
                        output_path=output,
                        name=name,
                        from_version=version,
                        to_version=version,
                    )

                    self.assertTrue(result["ok"])
                    self.assertEqual(version, result["from_version"])
                    self.assertEqual(version, result["to_version"])
                    self.assertEqual(before, json.loads(output.read_text(encoding="utf-8")))
                    self.assertEqual(before_hash, sha256(source))
                    self.assertFalse(
                        any(out_root.glob(f".{name}.zcloud-migrate-*")),
                        "migration staging artifact must be cleaned up",
                    )

    def test_round_trip_output_cannot_be_reused_as_overwrite_target(self):
        source = ROOT / "projects.json"
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "projects.json"
            migrate.write_migrated(
                input_path=source,
                output_path=output,
                name="projects.json",
                from_version=1,
                to_version=1,
            )
            first_hash = sha256(output)

            with self.assertRaisesRegex(
                migrate.ConfigMigrationError,
                "refusing to overwrite migration output",
            ):
                migrate.write_migrated(
                    input_path=source,
                    output_path=output,
                    name="projects.json",
                    from_version=1,
                    to_version=1,
                )

            self.assertEqual(first_hash, sha256(output))


if __name__ == "__main__":
    unittest.main()
