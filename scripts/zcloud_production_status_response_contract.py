"""Pure, non-authorizing production status response contract.

This module does not call GitHub, decide whether to deploy, or mutate the VPS.
It is a future integration primitive for issue #1160, deliberately separate
from the serialized production workflow owned by #1089.
"""
import re
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")


class InvalidProductionEvidence(ValueError):
    """Status evidence is incomplete, stale or associated with another commit."""


def validate_production_status_payload(payload: Any, candidate_sha: str) -> str:
    """Return 'success', 'failure', 'pending', or 'missing' for exact candidate.

    Never interpret malformed evidence as permission to deploy or skip deploy.
    Caller must separately verify that refs/heads/main remained unchanged
    before AND after the status lookup, and apply its own deployment policy.
    """
    if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
        raise InvalidProductionEvidence("invalid candidate SHA")
    if not isinstance(payload, dict) or payload.get("sha") != candidate_sha:
        raise InvalidProductionEvidence("status payload candidate SHA mismatch")
    statuses = payload.get("statuses")
    if not isinstance(statuses, list):
        raise InvalidProductionEvidence("missing or malformed statuses")
    matches = []
    for entry in statuses:
        if not isinstance(entry, dict):
            raise InvalidProductionEvidence("malformed status entry")
        if entry.get("context") != "zcloud/vps-production":
            continue
        state = entry.get("state")
        created_at = entry.get("created_at")
        identifier = entry.get("id")
        if state not in ("success", "failure", "pending", "error"):
            raise InvalidProductionEvidence("invalid production status state")
        if not isinstance(created_at, str) or not created_at:
            raise InvalidProductionEvidence("missing status timestamp")
        if type(identifier) is not int or identifier < 0:
            raise InvalidProductionEvidence("invalid status ID")
        matches.append(entry)
    if not matches:
        return "missing"
    latest = max(matches, key=lambda s: (s["created_at"], s["id"]))
    return "failure" if latest["state"] == "error" else latest["state"]
