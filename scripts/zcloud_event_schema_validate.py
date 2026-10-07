#!/usr/bin/env python3
"""Validate zCloud's versioned control-plane event row contract read-only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PRIVACY_CLASSES = {"safe_structured", "restricted_text", "never_export", "internal_key"}
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EventSchemaError(RuntimeError):
    pass


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventSchemaError(f"unable to read schema: {exc}") from exc
    if not isinstance(raw, dict):
        raise EventSchemaError("schema root must be an object")
    return raw


def validate_contract(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    storage = schema.get("storage")
    if not isinstance(storage, dict) or storage.get("table") != "runner_events":
        errors.append("storage.table must be runner_events")
        storage = {}

    columns = storage.get("columns")
    if not isinstance(columns, dict) or not columns:
        errors.append("storage.columns must be a non-empty object")
        columns = {}

    for name, spec in columns.items():
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            errors.append(f"invalid storage column identifier: {name!r}")
            continue
        if not isinstance(spec, dict):
            errors.append(f"storage column {name} must be an object")
            continue
        if spec.get("privacy") not in PRIVACY_CLASSES:
            errors.append(f"storage column {name} has invalid privacy class")
        if not isinstance(spec.get("required"), bool):
            errors.append(f"storage column {name} must declare required=true/false")

    raw_privacy = schema.get("privacy")
    privacy = raw_privacy if isinstance(raw_privacy, dict) else {}

    projection = schema.get("canonical_projection")
    if not isinstance(projection, dict) or projection.get("version") != 1:
        errors.append("canonical_projection.version must be 1")
        projection = {}
    fields = projection.get("fields")
    if not isinstance(fields, dict) or not fields:
        errors.append("canonical_projection.fields must be a non-empty object")
        fields = {}
    for field, spec in fields.items():
        if not isinstance(spec, dict):
            errors.append(f"canonical field {field} must be an object")
            continue
        source = spec.get("source")
        if source not in columns:
            errors.append(f"canonical field {field} references unknown source {source!r}")
            continue
        source_privacy = (columns.get(source) or {}).get("privacy")
        allowed_projection_classes = set(
            (privacy.get("canonical_projection_classes") or [])
        )
        if source_privacy not in allowed_projection_classes:
            errors.append(
                f"canonical field {field} may not project {source_privacy} source {source}"
            )

    if not isinstance(raw_privacy, dict):
        errors.append("privacy must be an object")
    projection_classes = set(privacy.get("canonical_projection_classes") or [])
    invalid_projection_classes = sorted(projection_classes - PRIVACY_CLASSES)
    if invalid_projection_classes:
        errors.append(
            "canonical_projection_classes contains invalid classes: "
            + ",".join(invalid_projection_classes)
        )
    if not projection_classes:
        errors.append("canonical_projection_classes must be non-empty")
    never_export = set(privacy.get("never_export_fields") or [])
    free_text = set(privacy.get("free_text_fields") or [])
    unknown_privacy_fields = sorted((never_export | free_text) - set(columns))
    if unknown_privacy_fields:
        errors.append(
            "privacy references unknown columns: " + ",".join(unknown_privacy_fields)
        )
    for name, spec in columns.items():
        if isinstance(spec, dict) and spec.get("privacy") == "never_export":
            if name not in never_export:
                errors.append(f"never_export column {name} missing from privacy list")

    event_contract = schema.get("event_type_contract")
    if not isinstance(event_contract, dict):
        errors.append("event_type_contract must be an object")
        event_contract = {}
    if not isinstance(event_contract.get("allow_unclassified_types"), bool):
        errors.append("event_type_contract.allow_unclassified_types must be boolean")
    pattern = event_contract.get("pattern")
    try:
        re.compile(str(pattern or ""))
    except re.error:
        errors.append("event_type_contract.pattern is invalid")
    families = event_contract.get("families")
    if not isinstance(families, dict) or not families:
        errors.append("event_type_contract.families must be a non-empty object")
    else:
        assigned: dict[str, str] = {}
        for family, values in families.items():
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                errors.append(f"event family {family} must be a string array")
                continue
            for event_type in values:
                if event_type in assigned:
                    errors.append(
                        f"event type {event_type} belongs to both {assigned[event_type]} and {family}"
                    )
                assigned[event_type] = str(family)

    targets = schema.get("semantic_target_types")
    if not isinstance(targets, list) or not targets or not all(
        isinstance(value, str) and value for value in targets
    ):
        errors.append("semantic_target_types must be a non-empty string array")

    return sorted(set(errors))


def _rfc3339(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EventSchemaError(f"{field} must be a non-empty RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventSchemaError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise EventSchemaError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def project_event_row(row: dict[str, Any] | sqlite3.Row, schema: dict[str, Any]) -> dict[str, Any]:
    errors = validate_contract(schema)
    if errors:
        raise EventSchemaError("event schema contract invalid: " + "; ".join(errors))

    fields = schema["canonical_projection"]["fields"]
    projected: dict[str, Any] = {}
    for field, spec in fields.items():
        source = str(spec["source"])
        try:
            value = row[source]
        except (KeyError, IndexError) as exc:
            raise EventSchemaError(f"event row missing source column {source}") from exc

        if value is None or (isinstance(value, str) and not value.strip()):
            if bool(spec.get("required")):
                raise EventSchemaError(f"event row missing required field {field}")
            projected[field] = None
            continue

        fmt = spec.get("format")
        if fmt == "rfc3339":
            value = _rfc3339(value, field)
        elif fmt == "kebab-case":
            pattern = re.compile(str(schema["event_type_contract"]["pattern"]))
            value = str(value).strip()
            if not pattern.fullmatch(value):
                raise EventSchemaError(f"{field} violates event type pattern")

        if "values" in spec and value not in spec["values"]:
            raise EventSchemaError(f"{field} has unsupported value {value!r}")
        if "minimum" in spec:
            try:
                if float(value) < float(spec["minimum"]):
                    raise EventSchemaError(
                        f"{field} must be >= {spec['minimum']}"
                    )
            except (TypeError, ValueError) as exc:
                raise EventSchemaError(f"{field} must be numeric") from exc

        projected[field] = value

    return projected


def _db_uri(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if path.is_symlink():
        raise EventSchemaError("database path must not be a symlink")
    if not resolved.is_file():
        raise EventSchemaError("database path must be a regular file")
    return f"file:{resolved.as_posix()}?mode=ro"


def _open_ro(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(_db_uri(path), uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise EventSchemaError(f"unable to open database read-only: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _storage_report(
    connection: sqlite3.Connection, schema: dict[str, Any]
) -> dict[str, Any]:
    storage = schema["storage"]
    expected: dict[str, dict[str, Any]] = storage["columns"]
    rows = connection.execute("PRAGMA table_info(runner_events)").fetchall()
    if not rows:
        raise EventSchemaError("runner_events table missing")

    actual = {str(row["name"]): str(row["type"] or "").upper() for row in rows}
    missing = sorted(
        name
        for name, spec in expected.items()
        if bool(spec.get("required")) and name not in actual
    )
    extras = sorted(set(actual) - set(expected))
    type_mismatches = sorted(
        {
            f"{name}:{actual[name]}!={str(spec.get('type') or '').upper()}"
            for name, spec in expected.items()
            if name in actual
            and str(spec.get("type") or "").upper()
            and actual[name] != str(spec.get("type") or "").upper()
        }
    )
    if extras and storage.get("allow_unclassified_columns") is True:
        extras = []

    quality = connection.execute(
        """
        SELECT
          COUNT(*) AS total_rows,
          SUM(CASE WHEN ts IS NULL OR trim(ts)='' OR unixepoch(ts) IS NULL THEN 1 ELSE 0 END) AS invalid_ts,
          SUM(CASE WHEN event IS NULL OR trim(event)='' THEN 1 ELSE 0 END) AS blank_event,
          SUM(CASE WHEN generating IS NULL OR generating NOT IN (0,1) THEN 1 ELSE 0 END) AS invalid_generating,
          SUM(CASE WHEN sending IS NULL OR sending NOT IN (0,1) THEN 1 ELSE 0 END) AS invalid_sending,
          SUM(CASE WHEN worker_slot IS NULL OR worker_slot < 1 THEN 1 ELSE 0 END) AS invalid_worker_slot,
          SUM(CASE WHEN assistant_chars IS NOT NULL AND assistant_chars < 0 THEN 1 ELSE 0 END) AS invalid_assistant_chars,
          SUM(CASE WHEN progress_at IS NOT NULL AND trim(progress_at)<>''
                    AND unixepoch(progress_at) IS NULL THEN 1 ELSE 0 END) AS invalid_progress_at
        FROM runner_events
        """
    ).fetchone()

    event_types = [
        str(row["event"])
        for row in connection.execute(
            "SELECT DISTINCT event FROM runner_events WHERE event IS NOT NULL AND trim(event)<>'' ORDER BY event"
        ).fetchall()
    ]
    pattern = re.compile(str(schema["event_type_contract"]["pattern"]))
    invalid_event_types = sorted(value for value in event_types if not pattern.fullmatch(value))

    family_map: dict[str, str] = {}
    for family, values in schema["event_type_contract"]["families"].items():
        for value in values:
            family_map[str(value)] = str(family)
    mapped = sorted(value for value in event_types if value in family_map)
    unclassified = sorted(value for value in event_types if value not in family_map)
    coverage = round((len(mapped) / len(event_types)) * 100.0, 2) if event_types else 100.0

    data_quality = {key: int(quality[key] or 0) for key in quality.keys()}
    quality_failures = {
        key: value
        for key, value in data_quality.items()
        if key != "total_rows" and value > 0
    }

    query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
    connection_total_changes = int(connection.total_changes)
    structural_ok = not missing and not extras and not type_mismatches
    quality_ok = not quality_failures and not invalid_event_types
    write_free = query_only and connection_total_changes == 0
    return {
        "ok": structural_ok and quality_ok and write_free,
        "query_only": query_only,
        "connection_total_changes": connection_total_changes,
        "table": "runner_events",
        "actual_columns": sorted(actual),
        "missing_required_columns": missing,
        "unclassified_columns": extras,
        "type_mismatches": type_mismatches,
        "data_quality": data_quality,
        "invalid_event_types": invalid_event_types[:25],
        "event_types": {
            "total": len(event_types),
            "classified": len(mapped),
            "unclassified": len(unclassified),
            "classification_coverage_pct": coverage,
            "unclassified_types": unclassified[:100],
        },
    }


def validate(
    schema_path: str | os.PathLike[str],
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    schema_file = Path(schema_path)
    schema = _read_schema(schema_file)
    contract_errors = validate_contract(schema)

    report: dict[str, Any] = {
        "ok": not contract_errors,
        "schema_id": schema.get("schema_id"),
        "schema_version": schema.get("schema_version"),
        "contract_errors": contract_errors,
        "canonical_fields": sorted(
            ((schema.get("canonical_projection") or {}).get("fields") or {}).keys()
        ),
        "privacy": {
            "canonical_projection_classes": list(
                ((schema.get("privacy") or {}).get("canonical_projection_classes") or [])
            ),
            "never_export_fields": list(
                ((schema.get("privacy") or {}).get("never_export_fields") or [])
            ),
            "free_text_fields": list(
                ((schema.get("privacy") or {}).get("free_text_fields") or [])
            ),
        },
        "semantic_target_types": list(schema.get("semantic_target_types") or []),
    }

    if db_path is not None:
        if contract_errors:
            report["storage"] = {"ok": False, "error": "contract invalid"}
        else:
            with _open_ro(Path(db_path)) as connection:
                storage = _storage_report(connection, schema)
            report["storage"] = storage
            report["ok"] = bool(report["ok"] and storage["ok"])

    return report


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Validate the zCloud versioned control-plane event contract."
    )
    parser.add_argument(
        "--schema",
        default=str(root / "schemas" / "control-plane-event-v1.json"),
        help="Path to event schema JSON.",
    )
    parser.add_argument("--db", help="Optional history.db path for read-only live conformance.")
    args = parser.parse_args()

    try:
        report = validate(args.schema, args.db)
    except (EventSchemaError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
