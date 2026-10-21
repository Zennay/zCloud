#!/usr/bin/env python3
"""Read-only, privacy-bounded worker progress history for zSentinel.

Browser-supplied events are observations, never proof of an active claim,
independent text generation, source merge or recovered worker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import sqlite3

PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SAFE_EVENTS = frozenset({
    "heartbeat", "prompt-sent", "generation-started", "generation-progress",
    "generation-finished", "runner-started", "runner-paused", "worker-paused",
    "worker-drained", "startup-blocked", "conversation-adopted",
    "target-tab-recovered", "quality-recovery-requested",
    "send-blocked", "composer-restored", "conversation-reset-requested",
})
# Allow only constant browser recovery reason codes in the operator view.
# Never return raw runner-status reason, URL, prompt, error or user text.
SAFE_REASON_CODES = frozenset({
    "composer-missing-manual-recovery-required",
    "composer-missing-operator-action-required",
    "composer-missing-before-first-send",
    "new-chat-button-missing",
    "composer-draft-preserved",
    "composer-text-not-confirmed",
    "composer-changed-before-send",
    # Producer PR #1552: bounded no-duplicate-dispatch manual review states.
    # Exact constant allowlist only; never reflect arbitrary browser reasons.
    "assignment-changed-before-send",
    "conversation-load-failed-manual-review",
    "previous-queue-send-after-reload-manual-review",
    "generation-unconfirmed-manual-review-no-retry",
    "dispatch-ledger-unavailable",
    "new-chat-transition-operator-action-required",
})
MAX_ROWS = 40


def _clock(value: object, reference: datetime) -> dict:
    """Return a bounded UTC clock reading without future-time clamping."""
    if not isinstance(value, str) or len(value) > 48:
        return {"status": "invalid", "utc": None, "age_s": None}
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("naive time")
        stamp = stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return {"status": "invalid", "utc": None, "age_s": None}
    age = (reference - stamp).total_seconds()
    if age < -120:
        return {"status": "future", "utc": stamp.isoformat(), "age_s": None}
    return {"status": "observed", "utc": stamp.isoformat(),
            "age_s": max(0, int(age))}


def _item(row: sqlite3.Row | None, reference: datetime) -> dict | None:
    if row is None:
        return None
    kind = row["event"]
    if not isinstance(kind, str) or kind not in SAFE_EVENTS:
        kind = "other_browser_event"
    raw_chars = row["assistant_chars"]
    chars = (raw_chars if type(raw_chars) is int and 0 <= raw_chars <= 2000000
             else None)
    raw_reason = row["reason"] if "reason" in row.keys() else None
    reason_code = (raw_reason if kind in {"send-blocked", "conversation-reset-requested"}
                   and isinstance(raw_reason, str)
                   and raw_reason in SAFE_REASON_CODES else None)
    return {
        "id": row["id"], "kind": kind, "reason_code": reason_code,
        "clock": _clock(row["ts"], reference), "reported_assistant_chars": chars,
    }


def read_worker_progress(db_path: Path, project: str, slot: int,
                         limit: int = 24) -> dict:
    if (not isinstance(project, str) or not PROJECT.fullmatch(project)
            or type(slot) is not int or not 1 <= slot <= 8
            or type(limit) is not int or not 1 <= limit <= MAX_ROWS):
        raise ValueError("invalid worker progress selection")
    reference = datetime.now(timezone.utc)
    result = {
        "schema": "zcloud-worker-progress-readonly-v1",
        "observed_at": reference.isoformat(), "project": project,
        "worker_slot": slot, "available": False,
        "coverage": "unavailable", "latest_event": None,
        "latest_prompt": None, "latest_progress": None,
        "latest_completion": None,
        "recent_events": [], "progress_since_latest_prompt": False,
        "progress_after_last_completion": False,
        "claim_bound": False, "claim_epoch_verified": False,
        "browser_progress_is_independent_proof": False,
        "worker_resumed_verified": False, "deployment_verified": False,
        "notes": "Browser telemetry only. Cannot prove queue ownership or visible generation.",
    }
    path = Path(db_path)
    # No schema initialization, expiry pruning, transaction or SQLite mutation.
    if path.is_symlink() or not path.is_file():
        return result
    uri = path.absolute().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            # Existing old DBs without this field must remain readable. No
            # database schema migration or mutation is performed by a GET.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runner_events)")}
            reason_column = "reason" if "reason" in columns else "NULL AS reason"
            fields = "id,ts,event,assistant_chars," + reason_column
            query = ("SELECT " + fields + " FROM runner_events "
                     "WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT ?")
            recent = connection.execute(query, (project, slot, limit)).fetchall()
            progress = connection.execute(
                "SELECT " + fields + " FROM runner_events "
                "WHERE project_id=? AND worker_slot=? AND event='generation-progress' "
                "ORDER BY id DESC LIMIT 1", (project, slot)).fetchone()
            prompt = connection.execute(
                "SELECT " + fields + " FROM runner_events "
                "WHERE project_id=? AND worker_slot=? AND event='prompt-sent' "
                "ORDER BY id DESC LIMIT 1", (project, slot)).fetchone()
            completion = connection.execute(
                "SELECT " + fields + " FROM runner_events "
                "WHERE project_id=? AND worker_slot=? AND event='generation-finished' "
                "ORDER BY id DESC LIMIT 1", (project, slot)).fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError):
        return result
    latest = _item(recent[0], reference) if recent else None
    current_progress = _item(progress, reference)
    current_prompt = _item(prompt, reference)
    current_completion = _item(completion, reference)
    after_prompt = bool(
        current_progress and current_prompt
        and current_progress["id"] > current_prompt["id"]
        and current_progress["clock"]["status"] == "observed"
    )
    result.update({
        "available": True, "coverage": "bounded_observation",
        "latest_event": latest, "latest_progress": current_progress,
        "latest_prompt": current_prompt,
        "latest_completion": current_completion,
        "progress_after_last_completion": bool(
            after_prompt and (not current_completion or
                              current_progress["id"] > current_completion["id"])
        ),
        "recent_events": [_item(row, reference) for row in recent],
        "progress_since_latest_prompt": after_prompt,
        "truncated": len(recent) >= limit,
    })
    return result


def render_html(data: dict) -> str:
    """Produce a self-contained, escaped and strictly non-authorizing operator view."""
    def clock_label(item):
        if not item:
            return "No signal recorded"
        value = item.get("clock") or {}
        status = value.get("status") or "invalid"
        stamp = value.get("utc") or "Unknown UTC time"
        age = value.get("age_s")
        suffix = f" ({age}s ago)" if type(age) is int else ""
        return escape(str(stamp) + suffix + " · " + str(status))

    facts = [
        ("Latest browser event", data.get("latest_event")),
        ("Latest prompt sent", data.get("latest_prompt")),
        ("Latest generated-text progress", data.get("latest_progress")),
        ("Latest response finished", data.get("latest_completion")),
    ]
    cards = []
    for label, record in facts:
        kind = escape(str((record or {}).get("kind") or "missing"))
        cards.append(
            "<article class='card'><h2>" + escape(label) + "</h2><b>" + kind
            + "</b><p>" + clock_label(record) + "</p></article>"
        )

    recent = []
    for item in data.get("recent_events") or []:
        if not isinstance(item, dict):
            continue
        event_id = item.get("id")
        if type(event_id) is not int:
            continue
        kind = escape(str(item.get("kind") or "other_browser_event"))
        chars = item.get("reported_assistant_chars")
        count = str(chars) if type(chars) is int else "not provided"
        recent.append(
            "<tr><td>" + str(event_id) + "</td><td>" + kind + "</td><td>"
            + clock_label(item) + "</td><td>" + count + "</td></tr>"
        )
    if not recent:
        recent = ["<tr><td colspan='4'>No bounded browser events available.</td></tr>"]

    project = escape(str(data.get("project") or ""), quote=True)
    slot = data.get("worker_slot")
    slot_text = str(slot) if type(slot) is int else "unknown"
    available = data.get("available") is True
    claim = "unverified"  # Never imply a browser observation proves a queue claim.
    progress = (
        "Recorded progress after the latest reported prompt."
        if data.get("progress_since_latest_prompt") is True
        else "No qualifying progress after the last reported prompt in this source."
    )
    history = (
        "Reported progress after last completion."
        if data.get("progress_after_last_completion") is True
        else "No reported progress after last completion."
    )
    css = (
        ":root{color-scheme:dark;font-family:system-ui,sans-serif;"
        "background:#101820;color:#ecf4fa}body{max-width:1100px;margin:auto;"
        "padding:clamp(15px,3vw,34px);line-height:1.5}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}"
        ".card,.notice{border:1px solid #425a69;border-radius:10px;"
        "background:#1a2834;padding:16px}.notice{border-left:4px solid #f5cc91}"
        ".muted{color:#b8c9d8}h1{font-size:clamp(1.55rem,4vw,2.4rem)}"
        "h2{font-size:1.04rem;margin-top:0}table{border-collapse:collapse;width:100%}"
        "th,td{text-align:left;border-bottom:1px solid #405564;padding:9px}"
        ".scroll{overflow-x:auto}strong{overflow-wrap:anywhere}"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none';"
        "style-src 'unsafe-inline'; form-action 'none'; base-uri 'none';\">"
        "<title>zSentinel · worker progress</title><style>" + css + "</style></head><body>"
        "<header><small class='muted'>ZCLOUD / ZSENTINEL · LOCAL OBSERVATION ONLY</small>"
        "<h1>Worker progress · " + project + " · slot " + slot_text + "</h1>"
        "<p class='muted'>Read-only browser telemetry · "
        + ("source available" if available else "source unavailable")
        + " · claim " + claim + " · bounded to "
        + str(len(data.get("recent_events") or [])) + " events</p></header>"
        "<section class='notice'><strong>No queue ownership or worker recovery verified.</strong>"
        "<p>A heartbeat, prompt or generated-text change is browser-declared;"
        " no source merge, deploy, current lease or independent generation is proven.</p></section>"
        "<section class='grid'>" + "".join(cards) + "</section>"
        "<section class='card'><h2>Recent activity</h2><p>"
        + escape(progress) + " " + escape(history) + "</p>"
        "<div class='scroll'><table><thead><tr><th>Event ID</th><th>Kind</th>"
        "<th>UTC clock status</th><th>Reported chars</th></tr></thead><tbody>"
        + "".join(recent) + "</tbody></table></div></section>"
        "<footer class='muted'><p>Partial browser-supplied sample. No credentials,"
        " conversation titles, text, links, prompts or tab identifiers displayed.</p></footer>"
        "</body></html>"
    )


def read_project_progress(db_path: Path, project: str, limit: int = 8) -> dict:
    """Show bounded activity for the eight existing worker slots, not claims.

    Each slot uses the original explicitly read-only reader. These are
    successive observations rather than one atomic portfolio snapshot; never
    infer active owner, release readiness or independent generation.
    """
    if (type(project) is not str or PROJECT.fullmatch(project) is None
            or type(limit) is not int or not 1 <= limit <= MAX_ROWS):
        raise ValueError("invalid project progress selection")
    slots = [read_worker_progress(db_path, project, slot, limit) for slot in range(1, 9)]
    available = sum(item["available"] is True for item in slots)
    signaled = sum(item["latest_event"] is not None for item in slots)
    return {
        "schema": "zcloud-worker-progress-overview-v1",
        "project": project,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "available": available > 0,
        "coverage": "read_only_sequential_nonatomic",
        "slots_considered": 8,
        "slots_source_available": available,
        "slots_with_observed_events": signaled,
        "slots": [{
            "slot": item["worker_slot"],
            "source_available": item["available"],
            "latest_event": item["latest_event"],
            "latest_prompt": item["latest_prompt"],
            "latest_progress": item["latest_progress"],
            "latest_completion": item["latest_completion"],
            "progress_since_latest_prompt": item["progress_since_latest_prompt"],
            "progress_after_last_completion": item["progress_after_last_completion"],
            "recent_events_sampled": len(item["recent_events"]),
            "sample_truncated": item.get("truncated", False),
        } for item in slots],
        "claim_bound": False,
        "claim_epoch_verified": False,
        "browser_progress_is_independent_proof": False,
        "worker_resumed_verified": False,
        "deployment_verified": False,
        "notes": ("Sequential, privacy-bounded browser signals across eight slots. "
                  "Missing signals do not prove an inactive worker or cleared queue."),
    }


def render_project_progress_html(data: dict) -> str:
    """Mobile-friendly eight-slot operator page with no external resources."""
    def age(item: dict | None) -> str:
        if not item:
            return "None observed"
        clock = item.get("clock") or {}
        status = clock.get("status", "unknown")
        stamp = clock.get("utc") or "UTC unavailable"
        seconds = clock.get("age_s")
        suffix = " / " + str(seconds) + "s old" if type(seconds) is int else ""
        return escape(str(stamp) + " / " + str(status) + suffix)

    rows = []
    for slot in data.get("slots", []):
        fields = [
            str(slot["slot"]),
            "available" if slot["source_available"] else "unavailable",
            str((slot["latest_event"] or {}).get("kind") or "none"),
            age(slot["latest_event"]),
            age(slot["latest_prompt"]),
            age(slot["latest_progress"]),
            age(slot["latest_completion"]),
            "reported" if slot["progress_since_latest_prompt"] else "not observed",
        ]
        rows.append("<tr>" + "".join("<td>" + escape(value) + "</td>"
                                      for value in fields) + "</tr>")
    if not rows:
        rows.append("<tr><td colspan='8'>No bounded slot observations.</td></tr>")
    summary = (str(data.get("slots_source_available", 0)) +
               " of 8 slot reads available; " +
               str(data.get("slots_with_observed_events", 0)) +
               " have any sampled browser event.")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none';"
        "style-src 'unsafe-inline'; form-action 'none'; base-uri 'none';\">"
        "<title>zSentinel eight-slot progress</title>"
        "<style>:root{color-scheme:dark;font-family:system-ui;background:#101820;color:#ecf4fa}"
        "body{max-width:1200px;margin:auto;padding:18px;line-height:1.5}"
        "table{width:100%;border-collapse:collapse}th,td{padding:9px;text-align:left;"
        "border-bottom:1px solid #405564}th{white-space:nowrap}"
        ".scroll{overflow-x:auto}.warn{border-left:4px solid #f5cc91;"
        "background:#1a2834;padding:14px}</style></head><body>"
        "<h1>zSentinel / " + escape(str(data.get("project") or "")) +
        " / 8 browser-worker slots</h1>"
        "<p>" + escape(summary) + "</p>"
        "<div class='warn'><strong>Not an active-claim or recovery report.</strong>"
        "<p>Browser-declared telemetry only, read from eight sequential "
        "SQLite read-only observations. A missing event is not evidence that "
        "a worker is stopped. Independent generation, queue lease, current "
        "GitHub CI, merge and production deployment remain unverified.</p></div>"
        "<div class='scroll'><table><thead><tr>"
        "<th>Slot</th><th>Source</th><th>Last kind</th>"
        "<th>Latest event UTC</th><th>Last prompt UTC</th>"
        "<th>Last progress UTC</th><th>Last completion UTC</th>"
        "<th>Progress after prompt</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div></body></html>"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only browser progress receipts")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent.parent / "history.db")
    parser.add_argument("--project", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--slot", type=int)
    selection.add_argument("--all-slots", action="store_true",
                           help="Show all eight known worker slots; browser signals only")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--format", choices=("json", "html"), default="json")
    args = parser.parse_args(argv)
    # An invalid oversized limit must not be silently accepted because
    # the eight-slot overview samples fewer rows per slot.
    if not 1 <= args.limit <= MAX_ROWS:
        parser.error("limit must be between 1 and 40")
    try:
        data = (read_project_progress(args.db, args.project, min(args.limit, 8))
                if args.all_slots else
                read_worker_progress(args.db, args.project, args.slot, args.limit))
    except ValueError:
        parser.error("invalid project, worker slot, or bounded limit")
    if args.format == "html":
        print(render_project_progress_html(data) if args.all_slots else render_html(data))
    else:
        print(json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if data["available"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
