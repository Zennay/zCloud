#!/usr/bin/env python3
"""Build a sanitized, line-scoped preview for serialized-gate PR metadata rewrites.

A future writer must not globally replace every retired-gate reference because
PR bodies can contain historical provenance. This read-only preview verifies the
immutable intent against fresh body/head evidence, changes only bounded current
serialized-coordination lines, and emits hashes/counts rather than body text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

INTENT_SCHEMA = "zcloud-serialized-gate-metadata-remediation-intent-v1"
SNAPSHOT_SCHEMA = "zcloud-serialized-gate-metadata-rewrite-preview-snapshot-v1"
OUTPUT_SCHEMA = "zcloud-serialized-gate-metadata-rewrite-preview-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_TARGETS = 50
MAX_PULLS = 500
MAX_BODY_CHARS = 100_000
MAX_CHANGED_LINES = 5
MAX_REPLACEMENTS = 10
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SERIALIZED_MARKERS = (
    "serialized",
    "keep unmerged",
    "integration window",
    "live writer",
    "production/control-plane window",
    "control-plane window",
)


class PreviewError(ValueError):
    pass


def _positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PreviewError(f"invalid {field}") from exc
    if number < 1:
        raise PreviewError(f"invalid {field}")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PreviewError(f"invalid {field}")
    return value


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise PreviewError(f"invalid {field}")
    return text


def _digest(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not DIGEST_RE.fullmatch(text):
        raise PreviewError(f"invalid {field}")
    return text


def _body(value: Any) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise PreviewError("PR body exceeds bounded input size")
    return text


def _body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise PreviewError(f"{label} symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise PreviewError(f"{label} unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise PreviewError(f"{label} exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreviewError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PreviewError(f"{label} must be a JSON object")
    return payload


def _serialized_line(line: str, *, primary: str, retired: str, successor: str) -> bool:
    lowered = line.lower()
    return (
        primary in lowered
        and retired in lowered
        and successor not in lowered
        and any(marker in lowered for marker in SERIALIZED_MARKERS)
    )


def build_preview(intent: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    if intent.get("schema") != INTENT_SCHEMA:
        raise PreviewError("unexpected intent schema")
    repository = str(intent.get("repository") or "").strip()
    if not repository or "/" not in repository or len(repository) > 200:
        raise PreviewError("invalid repository")
    current_main_sha = _sha(intent.get("current_main_sha"), "intent current_main_sha")
    status = str(intent.get("status") or "").strip()
    if status not in {"intent_ready", "clean"}:
        raise PreviewError("intent is not ready")
    if not _bool(intent.get("compare_and_swap_required"), "compare_and_swap_required"):
        raise PreviewError("intent must require compare-and-swap")
    if not _bool(intent.get("requires_fresh_body_hash_match"), "requires_fresh_body_hash_match"):
        raise PreviewError("intent must require fresh body hash")
    for field in (
        "metadata_write_authorized",
        "merge_authorized",
        "deploy_authorized",
        "mutation_performed",
    ):
        if _bool(intent.get(field), f"intent {field}"):
            raise PreviewError(f"intent unexpectedly sets {field}=true")

    raw_intents = intent.get("intents")
    if not isinstance(raw_intents, list) or len(raw_intents) > MAX_TARGETS:
        raise PreviewError("invalid bounded intent set")
    if int(intent.get("intent_count", -1)) != len(raw_intents):
        raise PreviewError("intent count mismatch")
    if status == "clean" and raw_intents:
        raise PreviewError("clean intent contains targets")

    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise PreviewError("unexpected snapshot schema")
    if str(snapshot.get("repository") or "").strip() != repository:
        raise PreviewError("intent/snapshot repository mismatch")
    if _sha(snapshot.get("current_main_sha"), "snapshot current_main_sha") != current_main_sha:
        raise PreviewError("intent main SHA is stale")

    primary_gate = _positive_int(snapshot.get("primary_gate"), "primary_gate")
    retired_gate = _positive_int(snapshot.get("retired_gate"), "retired_gate")
    successor_gate = _positive_int(snapshot.get("successor_gate"), "successor_gate")
    if len({primary_gate, retired_gate, successor_gate}) != 3:
        raise PreviewError("gate identities must be distinct")
    if not _bool(snapshot.get("successor_open"), "successor_open"):
        raise PreviewError("successor gate is no longer open")

    raw_pulls = snapshot.get("pull_requests")
    if not isinstance(raw_pulls, list) or len(raw_pulls) > MAX_PULLS:
        raise PreviewError("invalid bounded live PR inventory")
    live: dict[int, dict[str, Any]] = {}
    for raw in raw_pulls:
        if not isinstance(raw, dict):
            raise PreviewError("invalid live PR entry")
        number = _positive_int(raw.get("number"), "live PR number")
        if number in live:
            raise PreviewError(f"duplicate live PR #{number}")
        state = str(raw.get("state") or "").strip().lower()
        if state not in {"open", "closed"}:
            raise PreviewError(f"invalid live PR #{number} state")
        live[number] = {
            "state": state,
            "head_sha": _sha(raw.get("head_sha"), f"live PR #{number} head_sha"),
            "body": _body(raw.get("body")),
        }

    primary_marker = f"#{primary_gate}"
    retired_marker = f"#{retired_gate}"
    successor_marker = f"#{successor_gate}"
    previews: list[dict[str, Any]] = []
    seen: set[int] = set()

    for raw in raw_intents:
        if not isinstance(raw, dict):
            raise PreviewError("invalid remediation intent")
        number = _positive_int(raw.get("number"), "intent PR number")
        if number in seen:
            raise PreviewError(f"duplicate intent PR #{number}")
        seen.add(number)
        expected_head = _sha(raw.get("expected_head_sha"), f"intent PR #{number} head_sha")
        expected_body = _digest(raw.get("expected_body_sha256"), f"intent PR #{number} body digest")
        old_marker = str(raw.get("old_marker") or "")
        new_marker = str(raw.get("new_marker") or "")
        if old_marker != retired_marker or new_marker != successor_marker:
            raise PreviewError(f"intent PR #{number} marker mismatch")

        item = live.get(number)
        if item is None:
            raise PreviewError(f"intent PR #{number} missing from live snapshot")
        if item["state"] != "open":
            raise PreviewError(f"intent PR #{number} is no longer open")
        if item["head_sha"] != expected_head:
            raise PreviewError(f"intent PR #{number} head SHA changed")

        body = item["body"]
        observed_digest = _body_digest(body)
        if observed_digest != expected_body:
            raise PreviewError(f"intent PR #{number} body hash changed")

        lines = body.splitlines(keepends=True)
        changed_lines = 0
        replacement_count = 0
        proposed_lines: list[str] = []
        original_retired_count = body.count(retired_marker)

        for line in lines:
            if _serialized_line(
                line,
                primary=primary_marker,
                retired=retired_marker,
                successor=successor_marker,
            ):
                count = line.count(retired_marker)
                if count:
                    changed_lines += 1
                    replacement_count += count
                    line = line.replace(retired_marker, successor_marker)
            proposed_lines.append(line)

        if changed_lines < 1:
            raise PreviewError(f"intent PR #{number} has no line-scoped rewrite target")
        if changed_lines > MAX_CHANGED_LINES:
            raise PreviewError(f"intent PR #{number} exceeds changed-line bound")
        if replacement_count > MAX_REPLACEMENTS:
            raise PreviewError(f"intent PR #{number} exceeds replacement bound")

        proposed_body = "".join(proposed_lines)
        if proposed_body == body:
            raise PreviewError(f"intent PR #{number} preview made no change")
        if any(
            _serialized_line(
                line,
                primary=primary_marker,
                retired=retired_marker,
                successor=successor_marker,
            )
            for line in proposed_body.splitlines(keepends=True)
        ):
            raise PreviewError(f"intent PR #{number} retains stale serialized coordination")

        remaining_retired_count = proposed_body.count(retired_marker)
        if remaining_retired_count != original_retired_count - replacement_count:
            raise PreviewError(f"intent PR #{number} altered unrelated retired references")

        previews.append(
            {
                "number": number,
                "expected_head_sha": expected_head,
                "original_body_sha256": observed_digest,
                "proposed_body_sha256": _body_digest(proposed_body),
                "changed_line_count": changed_lines,
                "replacement_count": replacement_count,
                "preserved_retired_reference_count": remaining_retired_count,
            }
        )

    return {
        "schema": OUTPUT_SCHEMA,
        "repository": repository,
        "current_main_sha": current_main_sha,
        "status": "preview_ready" if previews else "clean",
        "preview_count": len(previews),
        "previews": previews,
        "line_scoped_rewrite_required": True,
        "compare_and_swap_required": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview line-scoped serialized-gate metadata rewrites without emitting body text"
    )
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = build_preview(
            _load_json(args.intent, "intent"),
            _load_json(args.snapshot, "snapshot"),
        )
    except PreviewError as exc:
        if args.json:
            print(json.dumps({
                "schema": OUTPUT_SCHEMA,
                "status": "invalid",
                "error": str(exc),
                "metadata_write_authorized": False,
                "merge_authorized": False,
                "deploy_authorized": False,
                "mutation_performed": False,
            }, sort_keys=True))
        else:
            print(f"SERIALIZED_GATE_METADATA_REWRITE_PREVIEW_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_METADATA_REWRITE_PREVIEW "
            f"status={result['status']} previews={result['preview_count']} "
            "metadata_write_authorized=false"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
