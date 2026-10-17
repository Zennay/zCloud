"""Offline-only negative authorization matrix for zCloud control-plane observations.

An observation is never a control action. This module deliberately exposes no
positive authorization path and must not be imported as a production gate.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DISALLOWED_ACTIONS = frozenset({
    "restart_worker", "force_push", "claim_queue", "release_queue",
    "deploy", "merge", "dispatch_workflow", "pause_worker", "resume_worker",
})

def classify_observation(payload: Any) -> dict[str, object]:
    """Return only bounded denial facts, never the original observation."""
    if not isinstance(payload, Mapping):
        reason = "invalid_payload"
    elif not isinstance(payload.get("action"), str):
        reason = "missing_action"
    elif payload["action"] not in DISALLOWED_ACTIONS:
        reason = "unknown_action"
    else:
        reason = "observation_not_authority"
    return {
        "authorized": False,
        "mutation_performed": False,
        "reason": reason,
    }
