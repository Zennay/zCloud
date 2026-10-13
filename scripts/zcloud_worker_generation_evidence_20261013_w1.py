#!/usr/bin/env python3
"""Fail-closed, read-only zCloud worker generation evidence.

Correlates queue allocation with /api/worker-debug telemetry; never sends
commands, repairs tabs or treats an "active" worker as generation proof.
No credential, prompt, URL, command, reason or raw payload is emitted.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 65536
MAX_WORKERS = 32
KEY = re.compile(r"^[a-z][a-z0-9-]{0,63}::w[1-9][0-9]{0,3}$")
ENDPOINT = "http://127.0.0.1:8765"
VERDICTS = {
    "generating", "prompt-sent", "no-heartbeat", "waiting", "idle:no-assignment",
    "paused", "blocked:client", "blocked:contract",
}


class EvidenceDenied(ValueError):
    """Missing, stale, contradictory or ambiguous observation."""


def _pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceDenied("duplicate_json_key")
        result[key] = value
    return result


def _load(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise EvidenceDenied("oversized_observation")
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(EvidenceDenied("nonfinite_json")))
    except EvidenceDenied:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise EvidenceDenied("malformed_json") from exc
    if not isinstance(payload, dict):
        raise EvidenceDenied("invalid_top_level")
    # json.loads accepts unpaired surrogate escapes and non-finite floats by default.
    # Reject those before they can cross evidence/provenance boundaries.
    def validate(value):
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise EvidenceDenied("invalid_decoded_unicode") from exc
        elif isinstance(value, float) and not math.isfinite(value):
            raise EvidenceDenied("nonfinite_json")
        elif isinstance(value, dict):
            for key, child in value.items():
                validate(key)
                validate(child)
        elif isinstance(value, list):
            for child in value:
                validate(child)
    validate(payload)
    return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise EvidenceDenied("redirect_denied")


def _fetch(path):
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(
        ENDPOINT + path,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "zcloud-worker-generation-evidence/1"},
    )
    try:
        with opener.open(request, timeout=5) as response:
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if response.status != 200 or media_type != "application/json":
                raise EvidenceDenied("invalid_http_response")
            raw = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EvidenceDenied("observation_unavailable") from exc
    return _load(raw)


def _allocation_fingerprint(targets):
    """Critical identity only; volatile memory/telemetry fields are excluded."""
    allocation = targets.get("global_allocation") if isinstance(targets, dict) else None
    rows = allocation.get("workers") if isinstance(allocation, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_WORKERS:
        raise EvidenceDenied("invalid_allocation_snapshot")
    seen_keys = set()
    seen_slots = set()
    identity = []
    for item in rows:
        if not isinstance(item, dict):
            raise EvidenceDenied("invalid_allocation_snapshot")
        key = _key(item.get("worker_key"))
        project, local_slot = key.rsplit("::w", 1)
        global_slot = item.get("global_worker_slot")
        queue_id = item.get("queue_id")
        provider = item.get("provider", "chatgpt")
        if (key in seen_keys or type(global_slot) is not int
                or not 1 <= global_slot <= MAX_WORKERS or global_slot in seen_slots
                or item.get("project_id") != project
                or type(item.get("worker_slot")) is not int
                or item["worker_slot"] != int(local_slot)
                or not isinstance(queue_id, str) or not 0 < len(queue_id) <= 160
                or provider not in ("chatgpt", "claude")):
            raise EvidenceDenied("ambiguous_allocation_snapshot")
        seen_keys.add(key)
        seen_slots.add(global_slot)
        identity.append((key, global_slot, queue_id, provider))
    return tuple(sorted(identity))


def _live_snapshots():
    # API endpoints are separately read, not an atomic SQLite snapshot. Bracket
    # the telemetry read and deny observed reassignment instead of false-green.
    before = _fetch("/api/runner-targets")
    middle = _fetch("/api/worker-debug")
    after = _fetch("/api/runner-targets")
    if _allocation_fingerprint(before) != _allocation_fingerprint(after):
        raise EvidenceDenied("allocation_changed_during_observation")
    return after, middle


def _instant(value):
    if not isinstance(value, str) or not value or len(value) > 48:
        raise EvidenceDenied("invalid_observation_time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceDenied("invalid_observation_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceDenied("naive_observation_time")
    return parsed.astimezone(timezone.utc)


def _key(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise EvidenceDenied("invalid_worker_identity")
    return value


def _age(value):
    if type(value) is not int or value < 0 or value > 86400 * 30:
        raise EvidenceDenied("invalid_telemetry_age")
    return value


def classify(targets, debug, *, at=None, max_age_seconds=120):
    """Return bounded sanitized worker findings; raises on unclear evidence.

    'generation_evidence' means fresh self-reported browser telemetry, not a
    verified on-screen DOM session or guaranteed future progress.
    """
    if not isinstance(targets, dict) or not isinstance(debug, dict):
        raise EvidenceDenied("invalid_observation_shape")
    if type(max_age_seconds) is not int or max_age_seconds < 1 or max_age_seconds > 900:
        raise EvidenceDenied("invalid_freshness_policy")
    now = at or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise EvidenceDenied("invalid_clock")
    timestamp = _instant(debug.get("time"))
    age = (now.astimezone(timezone.utc) - timestamp).total_seconds()
    if age < -10 or age > max_age_seconds:
        raise EvidenceDenied("stale_or_future_observation")
    allocation = targets.get("global_allocation")
    if not isinstance(allocation, dict) or not isinstance(allocation.get("workers"), list):
        raise EvidenceDenied("missing_allocation")
    allocated = allocation["workers"]
    if len(allocated) > MAX_WORKERS:
        raise EvidenceDenied("oversized_allocation")
    observed = debug.get("workers")
    if not isinstance(observed, list) or len(observed) > MAX_WORKERS * 8:
        raise EvidenceDenied("missing_worker_debug")
    by_key = {}
    for row in observed:
        if not isinstance(row, dict):
            raise EvidenceDenied("invalid_debug_row")
        key = _key(row.get("worker"))
        if key in by_key:
            raise EvidenceDenied("duplicate_debug_worker")
        by_key[key] = row

    findings = []
    seen = set()
    for item in allocated:
        if not isinstance(item, dict):
            raise EvidenceDenied("invalid_allocation_row")
        key = _key(item.get("worker_key"))
        if key in seen:
            raise EvidenceDenied("duplicate_allocated_worker")
        seen.add(key)
        project, slot = key.rsplit("::w", 1)
        if item.get("project_id") != project or type(item.get("worker_slot")) is not int or item["worker_slot"] != int(slot):
            raise EvidenceDenied("allocation_identity_mismatch")
        global_slot = item.get("global_worker_slot")
        if type(global_slot) is not int or not 1 <= global_slot <= MAX_WORKERS:
            raise EvidenceDenied("invalid_allocated_global_slot")
        queue_id = item.get("queue_id")
        if not isinstance(queue_id, str) or not queue_id or len(queue_id) > 160:
            raise EvidenceDenied("invalid_allocated_queue")
        provider = item.get("provider", "chatgpt")
        if provider not in ("chatgpt", "claude"):
            raise EvidenceDenied("unknown_provider")
        if provider == "claude":
            findings.append({"worker": key, "state": "other_provider", "generation_evidence": False})
            continue
        row = by_key.get(key)
        if row is None:
            findings.append({"worker": key, "state": "missing_worker_telemetry", "generation_evidence": False})
            continue
        if row.get("project") != project or type(row.get("slot")) is not int or row["slot"] != int(slot):
            raise EvidenceDenied("debug_identity_mismatch")
        # Prevent a successful generation heartbeat for a previous queue owner
        # being promoted as evidence for a freshly reassigned slot.
        if (type(row.get("global_slot")) is not int or row["global_slot"] != global_slot
                or row.get("queue_id") != queue_id or row.get("provider") != provider):
            raise EvidenceDenied("allocation_debug_binding_mismatch")
        verdict = row.get("verdict")
        if not isinstance(verdict, str) or verdict not in VERDICTS:
            raise EvidenceDenied("unknown_worker_verdict")
        if verdict == "generating":
            telemetry_age = _age(row.get("last_event_age_s"))
            # Backend's verdict depends on a fresh generating flag; also
            # validate the event category so a malformed/terminal event with
            # an inherited true flag never becomes a green generation proof.
            last_event = row.get("last_event")
            if last_event not in ("heartbeat", "generation-started"):
                state = "untrusted_generation_event"
            else:
                state = "generating_observed" if telemetry_age <= max_age_seconds else "stale_generation_telemetry"
        elif verdict == "prompt-sent":
            _age(row.get("last_prompt_age_s"))
            state = "prompt_sent_without_generation"
        elif verdict == "no-heartbeat":
            state = "missing_browser_heartbeat"
        elif verdict == "paused":
            state = "paused"
        elif verdict.startswith("blocked:"):
            state = "blocked"
        else:
            state = "not_generating"
        findings.append({
            "worker": key,
            "state": state,
            "generation_evidence": state == "generating_observed",
        })
    counts = {}
    for row in findings:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "schema": "zcloud-worker-generation-evidence-v1",
        "observation": "read_only",
        "control_authorized": False,
        "tab_presence_verified": False,
        "generation_directly_verified": False,
        "all_allocated_chatgpt_generating": bool(
            [x for x in findings if x["state"] != "other_provider"]
        ) and all(x["generation_evidence"] for x in findings if x["state"] != "other_provider"),
        "allocated_count": len(findings),
        "counts": counts,
        "workers": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-file", type=Path)
    parser.add_argument("--debug-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if bool(args.targets_file) != bool(args.debug_file):
            raise EvidenceDenied("both_fixtures_required")
        if args.targets_file:
            # Strict size check before reading untrusted fixture evidence.
            def file_bytes(path):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                    raise EvidenceDenied("invalid_fixture_path")
                with path.open("rb") as file:
                    return file.read(MAX_BYTES + 1)
            targets = _load(file_bytes(args.targets_file))
            debug = _load(file_bytes(args.debug_file))
        else:
            targets, debug = _live_snapshots()
        result = classify(targets, debug)
    except (EvidenceDenied, OSError) as exc:
        reason = str(exc) if isinstance(exc, EvidenceDenied) else "fixture_unavailable"
        print(json.dumps({"schema": "zcloud-worker-generation-evidence-v1", "status": "denied",
                          "reason": reason, "control_authorized": False,
                          "tab_presence_verified": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["all_allocated_chatgpt_generating"] else 1


if __name__ == "__main__":
    sys.exit(main())
