"""Deterministic capability choice for zCloud queue lanes; not an authorization grant.

Browser automation runs in-process with zSSH; never spawn a separate VPS agent.
Only target-paired tools can execute operations. A route hint does not bypass
zSSH authentication, operator approval, user-scoped allowlists or leases.
Ambiguous or unsupported routing fails closed to no tool.
"""
from __future__ import annotations

import json
import re

TARGET_PATTERN = re.compile(r"^zt_[A-Za-z0-9_-]{8,96}$")
BROWSER = (
    "playwright", "cdp", "browser", "webpage", "website", "dom",
    "accessibility tree", "tab navigation", "formulier",
)
# These are action-oriented system signals, not transport words such as zSSH
# or VPS that commonly appear in otherwise unambiguous browser tasks.
SYSTEM = (
    "systemctl", "systemd", "docker", "deploy", "runner", "filesystem",
    "shell command", "git pull", "restart service", "process list",
    "disk usage", "system logs",
)
TOOLS = {"browser": "zssh_browser", "system": "zssh", "none": None}


def _unique_object(pairs):
    """Reject duplicate JSON keys instead of accepting last-value-wins metadata."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate execution metadata key")
        result[key] = value
    return result


def parse_metadata_json(raw_metadata: str) -> dict:
    """Parse the canonical persisted metadata object without ambiguous duplicate keys."""
    value = json.loads(raw_metadata, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("execution metadata must be a JSON object")
    return value


def choose_execution_route(item: dict) -> dict:
    """Return a bounded tool hint; no command, credentials, consent or hostname."""
    metadata = item.get("metadata")
    metadata_invalid = False
    raw_metadata = item.get("metadata_json")
    if raw_metadata not in (None, ""):
        # Persisted JSON is canonical when present: a pre-parsed dict must not
        # bypass duplicate-key or malformed-JSON rejection.
        try:
            metadata = parse_metadata_json(raw_metadata)
        except (ValueError, TypeError):
            metadata = {}
            metadata_invalid = True
    elif not isinstance(metadata, dict):
        metadata_invalid = metadata is not None
        metadata = {}

    explicit_present = "execution_capability" in metadata
    raw_explicit = metadata.get("execution_capability")
    explicit = raw_explicit.strip().lower() if isinstance(raw_explicit, str) else ""
    if metadata_invalid:
        capability = "none"
        selection_source = "metadata_error"
        route_reason = "invalid_metadata_json"
    elif explicit_present and explicit not in ("none", "browser", "system"):
        # A present-but-empty or wrong-typed capability is a configuration
        # error, not permission to infer another execution route.
        capability = "none"
        selection_source = "explicit"
        route_reason = "unsupported_explicit_capability"
    elif explicit == "none":
        capability = "none"
        selection_source = "explicit"
        route_reason = "explicitly_disabled"
    elif explicit in ("browser", "system"):
        capability = explicit
        selection_source = "explicit"
        route_reason = "explicit_capability"
    else:
        task_text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "completion_criteria", "description")
        ).lower()
        browser_match = any(term in task_text for term in BROWSER)
        system_match = any(term in task_text for term in SYSTEM)
        selection_source = "heuristic"
        if browser_match and system_match:
            capability = "none"
            route_reason = "ambiguous_mixed_capability"
        elif browser_match:
            capability = "browser"
            route_reason = "browser_keywords_only"
        elif system_match:
            capability = "system"
            route_reason = "system_keywords_only"
        else:
            capability = "none"
            route_reason = "no_capability_signal"

    requested_target = str(metadata.get("zssh_target_id") or "").strip()
    target_id = requested_target if TARGET_PATTERN.fullmatch(requested_target) else None
    return {
        "capability": capability,
        "tool": TOOLS[capability],
        "target_id": target_id,
        "requires_pairing": True,
        "requires_operator_policy": capability == "browser",
        "selection_source": selection_source,
        "route_reason": route_reason,
        "authorized": False,
    }
