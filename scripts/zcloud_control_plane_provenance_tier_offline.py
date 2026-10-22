"""Offline, non-authorizing classification of control-plane evidence provenance.

This helper is intentionally not wired into the live scheduler, recovery endpoints,
queue, or deployment. A claimed source is not authenticated here.
"""
from __future__ import annotations

from typing import Mapping

KNOWN = frozenset({"notion_claim", "github_check", "github_main_status", "vps_process", "vps_sqlite", "runtime_generation"})
POLICY = {
    "notion_claim": ("claim_only", False),
    "github_check": ("ci_only", False),
    "github_main_status": ("main_status_only", False),
    "vps_process": ("process_only", False),
    "vps_sqlite": ("queue_snapshot_only", False),
    "runtime_generation": ("generation_claim_only", False),
}

def classify_provenance(observation: object) -> dict[str, object]:
    """Return bounded observational metadata; never grant control authority.

    A bare observation, including a self-described runtime_generation, cannot
    establish authenticated provenance or authorize any mutation.
    """
    denied = {"class": "unknown", "reason": "invalid_observation", "authenticated": False,
              "authorizes_restart": False, "authorizes_deploy": False,
              "authorizes_queue_write": False}
    if not isinstance(observation, Mapping):
        return denied
    source = observation.get("source")
    if not isinstance(source, str) or source not in KNOWN:
        return denied
    if type(observation.get("success")) is not bool:
        return denied
    classification, authenticated = POLICY[source]
    return {"class": classification if observation["success"] else "negative_observation",
            "reason": "self_declared_unverified",
            "authenticated": authenticated, "authorizes_restart": False,
            "authorizes_deploy": False, "authorizes_queue_write": False}
