#!/usr/bin/env python3
"""Classify why zCloud workers are not startable from the read-only status model.

The classifier intentionally emits only stable reason codes. Browser errors, auth
messages, prompts, task titles, claim metadata and conversation identifiers are
never copied into its output.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any

AUTH_TOKENS = (
    "auth",
    "login",
    "log in",
    "sign in",
    "session expired",
    "unauthorized",
    "forbidden",
)
BLOCK_PRIORITY = {
    "other": 0,
    "browser": 1,
    "auth": 2,
    "resource": 3,
    "claim": 4,
    "project_context": 5,
    "other": 6,
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _worker_signal_text(worker: dict[str, Any]) -> str:
    last = _as_dict(worker.get("last_event"))
    values = (
        last.get("event"),
        last.get("reason"),
        last.get("error"),
        worker.get("error"),
    )
    return " ".join(str(value or "") for value in values).lower()


def _add(blockers: list[dict[str, str]], category: str, code: str) -> None:
    item = {"category": category, "code": code}
    if item not in blockers:
        blockers.append(item)


def classify_worker_start_blockers(status: dict[str, Any]) -> dict[str, Any]:
    status = _as_dict(status)
    firefox = _as_dict(status.get("chatgpt_firefox"))
    firefox_active = firefox.get("active") is True
    dynamic = _as_dict(status.get("dynamic_workers"))
    memory = _as_dict(dynamic.get("memory_guard"))
    memory_healthy = (
        memory.get("healthy_for_new_worker") is True
        and int(memory.get("new_worker_capacity") or 0) > 0
    )
    runners = _as_dict(status.get("chatgpt_runners"))

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for project_id in sorted(runners):
        project = _as_dict(runners.get(project_id))
        for worker in _as_list(project.get("workers")):
            if not isinstance(worker, dict):
                continue
            worker_id = str(worker.get("worker_id") or "").strip()
            if not worker_id:
                continue

            desired_state = str(worker.get("desired_state") or "running").strip().lower()
            state = str(worker.get("state") or "unknown").strip().lower()
            signal = _worker_signal_text(worker)
            last_event = str(_as_dict(worker.get("last_event")).get("event") or "").strip().lower()
            blockers: list[dict[str, str]] = []

            if desired_state in {"paused", "draining"}:
                _add(blockers, "other", "worker_" + desired_state)
            else:
                if not firefox_active:
                    _add(blockers, "browser", "firefox_runtime_unavailable")
                elif state in {"offline", "stalled"}:
                    _add(blockers, "browser", "worker_tab_" + state)

                if any(token in signal for token in AUTH_TOKENS):
                    _add(blockers, "auth", "session_or_auth_required")

                if last_event == "assignment-invalid":
                    _add(blockers, "project_context", "assignment_contract_invalid")
                elif last_event == "assignment-refresh-failed":
                    if not any(token in signal for token in AUTH_TOKENS):
                        _add(blockers, "project_context", "assignment_refresh_failed")
                elif last_event == "thinking-effort-unavailable":
                    _add(blockers, "other", "thinking_effort_unavailable")
                elif last_event == "send-blocked":
                    _add(blockers, "other", "client_send_blocked")

                if worker.get("current_task") is None:
                    _add(blockers, "claim", "no_active_task_or_claim")

                if not memory_healthy and state in {"starting", "stale", "offline"}:
                    _add(blockers, "resource", "new_worker_memory_admission_blocked")

            blockers.sort(key=lambda item: (BLOCK_PRIORITY.get(item["category"], 99), item["code"]))
            for item in blockers:
                counts[item["category"]] = counts.get(item["category"], 0) + 1

            rows.append(
                {
                    "project_id": str(project_id),
                    "worker_id": worker_id,
                    "state": state,
                    "desired_state": desired_state,
                    "startable": not blockers,
                    "primary_blocker": blockers[0] if blockers else None,
                    "blockers": blockers,
                }
            )

    return {
        "schema_version": 1,
        "source": "api_status_read_model",
        "worker_count": len(rows),
        "blocked_worker_count": sum(1 for row in rows if row["blockers"]),
        "category_counts": dict(sorted(counts.items())),
        "workers": rows,
    }


def fetch_status(url: str, *, attempts: int = 3, timeout: float = 20.0) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("status endpoint returned a non-object payload")
            return payload
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2)
    raise RuntimeError("status endpoint unavailable for blocker classification") from last_error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify sanitized zCloud worker start blockers from /api/status."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8765/api/status")
    parser.add_argument("--status-json", help="Use a local status fixture instead of HTTP.")
    args = parser.parse_args()

    if args.status_json:
        with open(args.status_json, "r", encoding="utf-8") as handle:
            status = json.load(handle)
    else:
        status = fetch_status(args.url)

    print(json.dumps(classify_worker_start_blockers(status), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
