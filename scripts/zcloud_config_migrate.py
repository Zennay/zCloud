#!/usr/bin/env python3
"""Fail-closed schema-version and migration contract for zCloud config files."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CANONICAL_CONFIGS = (
    "projects.json",
    "project-layout.json",
    "resource-policy.json",
    "project-contracts.json",
)


class ConfigMigrationError(RuntimeError):
    pass


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigMigrationError(f"{path}: invalid JSON: {exc}") from exc


def load_registry(path: Path) -> dict:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ConfigMigrationError("version registry must be an object")
    if data.get("schema_version") != 1:
        raise ConfigMigrationError("version registry schema_version must be 1")
    configs = data.get("configs")
    if not isinstance(configs, dict):
        raise ConfigMigrationError("version registry configs must be an object")
    if set(configs) != set(CANONICAL_CONFIGS):
        missing = sorted(set(CANONICAL_CONFIGS) - set(configs))
        extra = sorted(set(configs) - set(CANONICAL_CONFIGS))
        raise ConfigMigrationError(
            f"version registry config set mismatch missing={missing} extra={extra}"
        )

    for name, spec in configs.items():
        if not isinstance(spec, dict):
            raise ConfigMigrationError(f"{name}: version spec must be an object")
        current = spec.get("current_version")
        if not isinstance(current, int) or isinstance(current, bool) or current < 1:
            raise ConfigMigrationError(f"{name}: current_version must be integer >= 1")
        source = spec.get("version_source")
        if source not in {"sidecar", "embedded"}:
            raise ConfigMigrationError(f"{name}: unsupported version_source {source!r}")
        if source == "embedded":
            field = spec.get("version_field")
            if not isinstance(field, str) or not field.strip():
                raise ConfigMigrationError(
                    f"{name}: embedded version source requires version_field"
                )
        elif "version_field" in spec:
            raise ConfigMigrationError(
                f"{name}: sidecar version source must not declare version_field"
            )
    return data


def current_document_version(name: str, document, spec: dict) -> int:
    current = int(spec["current_version"])
    if spec["version_source"] == "sidecar":
        return current
    if not isinstance(document, dict):
        raise ConfigMigrationError(f"{name}: embedded-version config must be an object")
    raw = document.get(spec["version_field"])
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigMigrationError(
            f"{name}: embedded {spec['version_field']} must be an integer"
        )
    return raw


def migrate_project_contracts_v0_to_v1(document):
    if not isinstance(document, dict):
        raise ConfigMigrationError("project-contracts.json v0 must be an object")
    if "schema_version" in document:
        raise ConfigMigrationError(
            "project-contracts.json v0 fixture must not already declare schema_version"
        )
    migrated = json.loads(json.dumps(document, ensure_ascii=False))
    migrated["schema_version"] = 1
    return migrated


MIGRATIONS = {
    "project-contracts.json": {
        (0, 1): migrate_project_contracts_v0_to_v1,
    },
}


def migration_plan(name: str, from_version: int, to_version: int) -> list[tuple[int, int]]:
    if name not in CANONICAL_CONFIGS:
        raise ConfigMigrationError(f"unsupported config {name!r}")
    if from_version < 0 or to_version < 0:
        raise ConfigMigrationError("schema versions must be non-negative")
    if from_version > to_version:
        raise ConfigMigrationError("downgrade migrations are not supported")
    plan = []
    version = from_version
    while version < to_version:
        edge = (version, version + 1)
        if edge not in MIGRATIONS.get(name, {}):
            raise ConfigMigrationError(
                f"{name}: missing migration edge {edge[0]}->{edge[1]}"
            )
        plan.append(edge)
        version += 1
    return plan


def migrate_document(name: str, document, from_version: int, to_version: int):
    value = json.loads(json.dumps(document, ensure_ascii=False))
    for edge in migration_plan(name, from_version, to_version):
        value = MIGRATIONS[name][edge](value)
    return value


def validate_current(root: Path, registry_path: Path) -> dict:
    registry = load_registry(registry_path)
    versions = {}
    for name in CANONICAL_CONFIGS:
        path = root / name
        if not path.is_file():
            raise ConfigMigrationError(f"{name}: canonical config missing")
        document = load_json(path)
        spec = registry["configs"][name]
        observed = current_document_version(name, document, spec)
        expected = int(spec["current_version"])
        if observed != expected:
            raise ConfigMigrationError(
                f"{name}: schema version mismatch expected={expected} observed={observed}"
            )
        versions[name] = observed
    return {"ok": True, "versions": versions}


def write_migrated(*, input_path: Path, output_path: Path, name: str, from_version: int, to_version: int) -> dict:
    if input_path.resolve() == output_path.resolve():
        raise ConfigMigrationError("refusing in-place config migration")
    source = load_json(input_path)
    migrated = migrate_document(name, source, from_version, to_version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "config": name,
        "from_version": from_version,
        "to_version": to_version,
        "output": str(output_path),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate or migrate versioned zCloud config")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--root", type=Path, required=True)
    validate_parser.add_argument("--registry", type=Path)
    validate_parser.add_argument("--json", action="store_true")

    migrate_parser = sub.add_parser("migrate")
    migrate_parser.add_argument("--config", required=True, choices=CANONICAL_CONFIGS)
    migrate_parser.add_argument("--from-version", type=int, required=True)
    migrate_parser.add_argument("--to-version", type=int, required=True)
    migrate_parser.add_argument("--input", type=Path, required=True)
    migrate_parser.add_argument("--output", type=Path, required=True)
    migrate_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            root = args.root.resolve()
            registry = (
                args.registry.resolve()
                if args.registry
                else root / "config-schema-versions.json"
            )
            result = validate_current(root, registry)
        else:
            result = write_migrated(
                input_path=args.input.resolve(),
                output_path=args.output.resolve(),
                name=args.config,
                from_version=args.from_version,
                to_version=args.to_version,
            )
    except ConfigMigrationError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"CONFIG_MIGRATION_BLOCKED: {exc}")
        return 2

    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
    else:
        print("CONFIG_MIGRATION_GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
