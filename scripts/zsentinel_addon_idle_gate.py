#!/usr/bin/env python3
"""Read-only, fail-closed zCloud browser worker idle admission.

Used by owner-approved live add-on installation and reload. This observer is
not an authorization source: workflow actor, main SHA, runner identity, review
and production lock remain independent mandatory gates. No queue/worker writes.
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen

RUNNER_URL = "http://127.0.0.1:8765/api/runner-live"
MIN_STABLE_SECONDS = 12
MAX_PROJECTS = 50
MAX_WORKERS = 200
MAX_EVENT_AGE_SECONDS = 90
MAX_FUTURE_EVENT_SECONDS = 5


class IdleNotProven(ValueError):
    pass


def _read_worker_snapshot() -> frozenset[str]:
    with urlopen(RUNNER_URL, timeout=8) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise IdleNotProven("invalid runner response")
    projects = data.get("chatgpt_runners")
    if not isinstance(projects, dict) or not projects or len(projects) > MAX_PROJECTS:
        raise IdleNotProven("missing or unbounded runner projects")
    active: set[str] = set()
    observed = 0
    for project in projects.values():
        if not isinstance(project, dict):
            raise IdleNotProven("malformed project snapshot")
        workers = project.get("workers")
        if not isinstance(workers, list):
            raise IdleNotProven("missing worker snapshot")
        for worker in workers:
            observed += 1
            if observed > MAX_WORKERS or not isinstance(worker, dict):
                raise IdleNotProven("malformed or unbounded worker list")
            if not isinstance(worker.get("active"), bool):
                raise IdleNotProven("worker activity unknown")
            if not worker["active"]:
                continue
            # A missing/offline/stale worker is UNKNOWN, never proof of idle.
            # The server derives state=live from the last heartbeat/event.
            if worker.get("state") != "live":
                raise IdleNotProven("active worker telemetry stale or unavailable")
            age = worker.get("age_seconds")
            if (not isinstance(age, (int, float)) or not math.isfinite(age)
                    or age < 0 or age > MAX_EVENT_AGE_SECONDS):
                raise IdleNotProven("active worker age unknown")
            event = worker.get("last_event")
            if not isinstance(event, dict):
                raise IdleNotProven("active worker last event unknown")
            raw_ts = event.get("time")
            if not isinstance(raw_ts, str):
                raise IdleNotProven("active worker last event clock unknown")
            try:
                event_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if event_ts.tzinfo is None:
                    raise ValueError("timezone required")
                event_seconds = (datetime.now(timezone.utc) - event_ts).total_seconds()
            except ValueError:
                raise IdleNotProven("invalid event clock") from None
            if (event_seconds < -MAX_FUTURE_EVENT_SECONDS
                    or event_seconds > MAX_EVENT_AGE_SECONDS):
                raise IdleNotProven("active worker event clock outside freshness window")
            identity = worker.get("worker_id")
            if not isinstance(identity, (str, int)) or not str(identity).strip():
                raise IdleNotProven("active worker identity unknown")
            identity = str(identity)
            if identity in active:
                raise IdleNotProven("colliding active worker identities")
            active.add(identity)
            if worker.get("generating") is not False or worker.get("sending") is not False:
                raise IdleNotProven("worker busy or progress unknown")
            if worker.get("desired_state") == "draining":
                raise IdleNotProven("worker draining")
            command = worker.get("command")
            if command is not None and not isinstance(command, dict):
                raise IdleNotProven("worker command state unknown")
            if isinstance(command, dict) and command.get("status") == "pending":
                raise IdleNotProven("worker command pending")
            last = event.get("event")
            if last in {"prompt-sent", "generation-started"}:
                if not isinstance(age, (int, float)) or not math.isfinite(age) or age < 180:
                    raise IdleNotProven("recent generation not proven finished")
    if observed == 0:
        raise IdleNotProven("no observed workers")
    return frozenset(active)


def main() -> int:
    try:
        first = _read_worker_snapshot()
        time.sleep(MIN_STABLE_SECONDS)
        second = _read_worker_snapshot()
        if first != second:
            raise IdleNotProven("worker allocation changed during idle window")
    except IdleNotProven as error:
        # Source-defined reasons only; never print raw API data or worker IDs.
        busy = {
            "worker busy or progress unknown",
            "worker draining",
            "worker command pending",
            "recent generation not proven finished",
        }
        changing = {"worker allocation changed during idle window", "colliding active worker identities"}
        reason = str(error)
        category = "BUSY" if reason in busy else "CHANGED" if reason in changing else "SOURCE_UNVERIFIABLE"
        print(f"WORKER_ADDON_IDLE_DENIED={category}")
        return 1
    except (OSError, TimeoutError):
        print("WORKER_ADDON_IDLE_DENIED=SOURCE_UNAVAILABLE")
        return 1
    except (ValueError, KeyError, TypeError):
        print("WORKER_ADDON_IDLE_DENIED=MALFORMED_SOURCE")
        return 1
    print("WORKER_ADDON_IDLE_PROVEN=1")
    print(f"OBSERVED_ACTIVE_WORKER_COUNT={len(second)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
