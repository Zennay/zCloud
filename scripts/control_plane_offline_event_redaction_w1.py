"""Offline-only fail-closed control-plane evidence redactor.

This reference utility is NOT wired to production logging or a permission gate.
Only an allowlist of scalar operational metadata may leave the boundary.
"""
from collections.abc import Mapping
import re

SAFE_FIELDS = frozenset({"event", "project", "worker", "status", "timestamp"})
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
SAFE_TIME = re.compile(r"^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$")
SAFE_STATUS = frozenset({"queued", "running", "success", "failed", "cancelled", "unknown"})


def redact_control_event(event):
    """Return strictly allowlisted metadata; reject malformed records.

    In particular never echo errors, URLs, headers, prompts, environment,
    cookies, payloads, bearer tokens, or arbitrary unknown data.
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
        elif not SAFE_TOKEN.fullmatch(value):
            raise ValueError(f"invalid {key}")
        if key == "status" and value not in SAFE_STATUS:
            raise ValueError("invalid status")
        clean[key] = value
    return clean
