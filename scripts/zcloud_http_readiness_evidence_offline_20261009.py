"""Pure, offline classifier for HTTP readiness observations.

This module is intentionally NOT integrated with runtime recovery logic.
An HTTP response alone never authorizes service restart or deployment.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReadinessEvidence:
    state: str
    reason: str
    authorizes_restart: bool = False


def classify_http_readiness(status: Optional[int], *, service_active: Optional[bool], sqlite_ok: Optional[bool]) -> ReadinessEvidence:
    """Classify a single observation without inferring causality.

    Unknown service or SQLite state cannot be treated as proof of health.
    A transient 503 despite healthy service+SQLite indicates a readiness
    mismatch that should be sampled again, never a restart command.
    """
    if type(status) is not int or not 100 <= status <= 599:
        return ReadinessEvidence("incomplete", "invalid_http_status")
    if type(service_active) is not bool or type(sqlite_ok) is not bool:
        return ReadinessEvidence("incomplete", "missing_dependency_evidence")
    if not service_active:
        return ReadinessEvidence("unavailable", "service_inactive")
    if not sqlite_ok:
        return ReadinessEvidence("degraded", "sqlite_probe_failed")
    if 200 <= status <= 299:
        return ReadinessEvidence("ready", "http_success")
    if status in (502, 503, 504):
        return ReadinessEvidence("transient", "http_gateway_or_unavailable")
    return ReadinessEvidence("degraded", "http_non_success")
