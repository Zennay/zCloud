"""Offline, fail-closed exact-commit status response guard.

No GitHub API, file-system or deployment side effects.
"""
import re
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")


class StatusProofRejected(ValueError):
    """Status response cannot prove the exact candidate revision."""


def validate_status_response(candidate_sha: str, payload: Any) -> dict:
    """Return verified payload only when the queried revision is exact.

    Reject missing, symbolic, malformed, uppercase, stale, or mismatched SHAs.
    This function does not interpret context success or authorize deployment.
    """
    if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
        raise StatusProofRejected("invalid_candidate_sha")
    if not isinstance(payload, dict):
        raise StatusProofRejected("invalid_status_payload")
    response_sha = payload.get("sha")
    if not isinstance(response_sha, str) or not _SHA.fullmatch(response_sha):
        raise StatusProofRejected("invalid_response_sha")
    if response_sha != candidate_sha:
        raise StatusProofRejected("response_sha_mismatch")
    if not isinstance(payload.get("statuses"), list):
        raise StatusProofRejected("invalid_statuses")
    return payload
