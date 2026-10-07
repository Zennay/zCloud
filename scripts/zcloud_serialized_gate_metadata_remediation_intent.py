#!/usr/bin/env python3
"""Build immutable, read-only intent manifests for serialized-gate metadata remediation.

A preflight can prove that a target is currently stale, but metadata can still
change before a future writer acts. This builder binds each proposed marker
replacement to the exact observed PR head SHA and SHA-256 digest of the PR body.
It never emits the body itself and never authorizes or performs a write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

PREFLIGHT_SCHEMA = "zcloud-serialized-gate-metadata-batch-preflight-v1"
SNAPSHOT_SCHEMA = "zcloud-serialized-gate-metadata-remediation-intent-snapshot-v1"
OUTPUT_SCHEMA = "zcloud-serialized-gate-metadata-remediation-intent-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_TARGETS = 50
MAX_PULLS = 500
MAX_BODY_CHARS = 100_000
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


class IntentError(ValueError):
    pass


def _positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise IntentError(f"invalid {field}") from exc
    if number < 1:
        raise IntentError(f"invalid {field}")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise IntentError(f"invalid {field}")
    return value


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(text):
        raise IntentError(f"invalid {field}")
    return text


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise IntentError(f"{label} symlink rejected")
    try:
        stat = path.stat()
    except OSError as exc:
        raise IntentError(f"{label} unavailable: {exc}") from exc
    if stat.st_size > MAX_INPUT_BYTES:
        raise IntentError(f"{label} exceeds bounded input size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntentError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise IntentError(f"{label} must be a JSON object")
    return payload


def _body(value: Any) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise IntentError("PR body exceeds bounded input size")
    return text


def _serialized_context(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in SERIALIZED_MARKERS)


def _body_digest(body: str) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if not DIGEST_RE.fullmatch(digest):
        raise IntentError("invalid body digest")
    return digest


def build_intent(
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    primary_gate: int = 580,
    retired_gate: int = 576,
    successor_gate: int = 1089,
) -> dict[str, Any]:
    if preflight.get("schema") != PREFLIGHT_SCHEMA:
        raise IntentError("unexpected preflight schema")
    repository = str(preflight.get("repository") or "").strip()
    if not repository or "/" not in repository or len(repository) > 200:
        raise IntentError("invalid repository")
    current_main_sha = _sha(preflight.get("current_main_sha"), "preflight current_main_sha")
    status = str(preflight.get("status") or "").strip()
    if status not in {"ready_for_review", "clean"}:
        raise IntentError("preflight is not ready")

    for field in (
        "metadata_write_authorized",
        "merge_authorized",
        "deploy_authorized",
        "mutation_performed",
    ):
        if _bool(preflight.get(field), f"preflight {field}"):
            raise IntentError(f"preflight unexpectedly sets {field}=true")
    if not _bool(preflight.get("requires_fresh_revalidation"), "requires_fresh_revalidation"):
        raise IntentError("preflight must require fresh revalidation")

    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise IntentError("unexpected snapshot schema")
    if str(snapshot.get("repository") or "").strip() != repository:
        raise IntentError("preflight/snapshot repository mismatch")
    if _sha(snapshot.get("current_main_sha"), "snapshot current_main_sha") != current_main_sha:
        raise IntentError("preflight main SHA is stale")
    if _positive_int(snapshot.get("primary_gate"), "snapshot primary_gate") != primary_gate:
        raise IntentError("primary gate mismatch")
    if _positive_int(snapshot.get("retired_gate"), "snapshot retired_gate") != retired_gate:
        raise IntentError("retired gate mismatch")
    if _positive_int(snapshot.get("successor_gate"), "snapshot successor_gate") != successor_gate:
        raise IntentError("successor gate mismatch")
    if not _bool(snapshot.get("successor_open"), "snapshot successor_open"):
        raise IntentError("successor gate is no longer open")

    eligible = preflight.get("eligible")
    if not isinstance(eligible, list) or len(eligible) > MAX_TARGETS:
        raise IntentError("invalid bounded preflight eligible set")
    if status == "clean" and eligible:
        raise IntentError("clean preflight contains eligible targets")
    if int(preflight.get("ineligible_count", -1)) != 0:
        raise IntentError("preflight contains ineligible targets")
    if int(preflight.get("eligible_count", -1)) != len(eligible):
        raise IntentError("eligible count mismatch")

    raw_pulls = snapshot.get("pull_requests")
    if not isinstance(raw_pulls, list) or len(raw_pulls) > MAX_PULLS:
        raise IntentError("invalid bounded live PR inventory")
    live: dict[int, dict[str, Any]] = {}
    for raw in raw_pulls:
        if not isinstance(raw, dict):
            raise IntentError("invalid live PR entry")
        number = _positive_int(raw.get("number"), "live PR number")
        if number in live:
            raise IntentError(f"duplicate live PR #{number}")
        state = str(raw.get("state") or "").strip().lower()
        if state not in {"open", "closed"}:
            raise IntentError(f"invalid live PR #{number} state")
        live[number] = {
            "state": state,
            "head_sha": _sha(raw.get("head_sha"), f"live PR #{number} head_sha"),
            "body": _body(raw.get("body")),
        }

    primary_marker = f"#{primary_gate}"
    retired_marker = f"#{retired_gate}"
    successor_marker = f"#{successor_gate}"
    intents: list[dict[str, Any]] = []
    seen: set[int] = set()

    for raw in eligible:
        if not isinstance(raw, dict):
            raise IntentError("invalid eligible target")
        number = _positive_int(raw.get("number"), "eligible PR number")
        if number in seen:
            raise IntentError(f"duplicate eligible PR #{number}")
        seen.add(number)
        expected_head = _sha(raw.get("head_sha"), f"eligible PR #{number} head_sha")
        item = live.get(number)
        if item is None:
            raise IntentError(f"eligible PR #{number} missing from live snapshot")
        if item["state"] != "open":
            raise IntentError(f"eligible PR #{number} is no longer open")
        if item["head_sha"] != expected_head:
            raise IntentError(f"eligible PR #{number} head SHA changed")

        body = item["body"]
        lowered = body.lower()
        if successor_marker in lowered:
            raise IntentError(f"eligible PR #{number} is already successor-aware")
        if retired_marker not in lowered:
            raise IntentError(f"eligible PR #{number} no longer references retired gate")
        if primary_marker not in lowered or not _serialized_context(body):
            raise IntentError(f"eligible PR #{number} lost serialized context")

        intents.append(
            {
                "number": number,
                "expected_head_sha": expected_head,
                "expected_body_sha256": _body_digest(body),
                "old_marker": retired_marker,
                "new_marker": successor_marker,
            }
        )

    return {
        "schema": OUTPUT_SCHEMA,
        "repository": repository,
        "current_main_sha": current_main_sha,
        "status": "intent_ready" if intents else "clean",
        "intent_count": len(intents),
        "intents": intents,
        "compare_and_swap_required": True,
        "requires_fresh_body_hash_match": True,
        "metadata_write_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build immutable body-hash-bound remediation intents without mutating PR metadata"
    )
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = build_intent(
            _load_json(args.preflight, "preflight"),
            _load_json(args.snapshot, "snapshot"),
        )
    except IntentError as exc:
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
            print(f"SERIALIZED_GATE_METADATA_INTENT_INVALID: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print(
            "SERIALIZED_GATE_METADATA_INTENT "
            f"status={result['status']} "
            f"intents={result['intent_count']} "
            "metadata_write_authorized=false"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
