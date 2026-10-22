"""Deterministic capability choice for zCloud queue lanes; not an authorization grant.

Browser automation runs in-process with zSSH; never spawn a separate VPS agent.
Only target-paired tools can execute operations. A route hint does not bypass
zSSH authentication, operator approval, user-scoped allowlists or leases.
"""
from __future__ import annotations
import json
import re

TARGET_PATTERN = re.compile(r"^zt_[A-Za-z0-9_-]{8,96}$")
BROWSER = ("playwright", "cdp", "browser", "webpage", "website", "dom", "accessibility tree", "tab navigation", "formulier")
SYSTEM = ("zssh", "ssh", "vps", "systemctl", "systemd", "docker", "deploy", "runner", "filesystem", "shell", "git pull")
TOOLS = {"browser": "zssh_browser", "system": "zssh", "none": None}


def choose_execution_route(item: dict) -> dict:
    """Return a bounded tool hint; no command, credentials, consent or hostname."""
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        try:
            metadata = json.loads(item.get("metadata_json") or "{}")
        except (ValueError, TypeError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    explicit = str(metadata.get("execution_capability") or "").strip().lower()
    text = " ".join(str(item.get(key) or "") for key in ("title", "completion_criteria", "description")).lower()
    # Explicit disabling takes precedence. Heuristic only chooses the capability,
    # never authorizes execution or attaches to a target.
    if explicit in TOOLS:
        capability = explicit
    elif any(term in text for term in BROWSER):
        capability = "browser"
    elif any(term in text for term in SYSTEM):
        capability = "system"
    else:
        capability = "none"
    requested_target = str(metadata.get("zssh_target_id") or "").strip()
    target_id = requested_target if TARGET_PATTERN.fullmatch(requested_target) else None
    return {
        "capability": capability,
        "tool": TOOLS[capability],
        "target_id": target_id,
        "requires_pairing": True,
        "requires_operator_policy": capability == "browser",
        "authorized": False,
    }
