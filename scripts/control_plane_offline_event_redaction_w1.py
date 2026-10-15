"""Offline-only fail-closed control-plane evidence redactor.

This reference utility is NOT wired to production logging or a permission gate.
Only an allowlist of scalar operational metadata may leave the boundary.
"""
from collections.abc import Mapping
from datetime import datetime
import re

SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
SAFE_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SAFE_STATUS = frozenset({"queued", "running", "success", "failed", "cancelled", "unknown"})


def redact_control_event(event):
    """Return strictly allowlisted metadata; reject malformed records.

    Never echo errors, URLs, headers, prompts, environment, cookies,
    payloads, bearer tokens, or arbitrary unknown data.
    """
    if not isinstance(event, Mapping):
        raise ValueError("event must be an object")
    clean = {}
    for key in ("event", "project", "worker", "status", "timestamp"):
        value = event.get(key)
        if not isinstance(value, str):
            raise ValueError(f"missing or invalid {key}")
        if key == "timestamp":
            if not SAFE_TIME.fullmatch(value):
                raise ValueError("invalid timestamp")
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                raise ValueError("invalid timestamp") from None
        elif not SAFE_TOKEN.fullmatch(value):
            raise ValueError(f"invalid {key}")
        if key == "status" and value not in SAFE_STATUS:
            raise ValueError("invalid status")
        clean[key] = value
    return clean
