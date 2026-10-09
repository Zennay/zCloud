"""Offline consumer-side classification for task-claim GET observations.

No networking, database, worker admission or production imports. This module
intentionally NEVER returns an authorization decision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClaimObservation:
    state: str
    claims: tuple[dict[str, Any], ...] = ()
    reason: str = ""


def classify_claim_get(*, http_status: int | None, body: str | None,
                       project_id: str) -> ClaimObservation:
    """Classify an HTTP snapshot; absence is never proof of free ownership."""
    if not isinstance(project_id, str) or not project_id.strip():
        return ClaimObservation("unknown", reason="missing_project")
    if http_status != 200:
        return ClaimObservation("unknown", reason="http_untrusted")
    if not isinstance(body, str):
        return ClaimObservation("unknown", reason="missing_body")
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return ClaimObservation("unknown", reason="invalid_json")
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        return ClaimObservation("unknown", reason="invalid_shape")
    claims = payload["claims"]
    if any(not isinstance(c, dict) or c.get("project_id") != project_id
           or not isinstance(c.get("claim_key"), str)
           or not isinstance(c.get("owner_id"), str)
           for c in claims):
        return ClaimObservation("unknown", reason="invalid_claim")
    return ClaimObservation("observed", tuple(claims), "snapshot_only")
