"""Offline-only production-status SHA binding contract.

No API, network, deployment or authorization side effects. This module deliberately
does not modify the live deploy workflow; #1089 owns that production path.
"""
import re

_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class StatusEvidenceError(ValueError):
    """Fail-closed status evidence mismatch."""


def verify_production_status(candidate_sha, before_sha, after_sha, payload, context="zcloud/vps-production"):
    """Return whether the *same immutable candidate* already has a green status.

    A false return means no matching success status. It is NOT deploy authorization.
    Invalid or non-atomic evidence raises StatusEvidenceError, never an allow decision.
    """
    if any(not isinstance(s, str) or not _SHA.fullmatch(s) for s in (candidate_sha, before_sha, after_sha)):
        raise StatusEvidenceError("invalid candidate/main SHA")
    candidate, before, after = (s.lower() for s in (candidate_sha, before_sha, after_sha))
    if candidate != before or before != after:
        raise StatusEvidenceError("main drift or candidate mismatch")
    if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str) or not _SHA.fullmatch(payload["sha"]):
        raise StatusEvidenceError("missing or invalid response SHA")
    if payload["sha"].lower() != candidate:
        raise StatusEvidenceError("response SHA mismatch")
    statuses = payload.get("statuses")
    if not isinstance(statuses, list):
        raise StatusEvidenceError("missing statuses list")
    for item in statuses:
        if not isinstance(item, dict) or not isinstance(item.get("context"), str) or not isinstance(item.get("state"), str):
            raise StatusEvidenceError("malformed status entry")
    relevant = [s for s in statuses if s["context"] == context]
    return bool(relevant and relevant[0]["state"] == "success")
