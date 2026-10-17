#!/usr/bin/env python3
"""Offline control-plane incident tabletop classification. No network or writes."""
from __future__ import annotations

import argparse
import json
from typing import Any

ALLOWED = frozenset({"healthy", "unhealthy", "unknown"})
TRUST = frozenset({"trusted-main", "pr-only", "unobserved"})


def classify(evidence: dict[str, Any]) -> dict[str, str | bool]:
    """Fail closed when provenance or time-aligned evidence is insufficient."""
    service = evidence.get("service", "unknown")
    api = evidence.get("api", "unknown")
    generation = evidence.get("generation", "unknown")
    provenance = evidence.get("provenance", "unobserved")
    if not all(v in ALLOWED for v in (service, api, generation)):
        raise ValueError("service/api/generation must be healthy, unhealthy or unknown")
    if provenance not in TRUST:
        raise ValueError("invalid provenance")
    backlog = evidence.get("queued_jobs", 0)
    if type(backlog) is not int or backlog < 0:
        raise ValueError("queued_jobs must be a nonnegative integer")
    writer = evidence.get("serialized_writer_active", False)
    if type(writer) is not bool:
        raise ValueError("serialized_writer_active must be boolean")

    if writer:
        return {"classification": "writer-gated", "next_step": "respect-owner-gate",
                "production_mutation_authorized": False}
    if provenance != "trusted-main":
        return {"classification": "insufficient-production-evidence",
                "next_step": "obtain-trusted-main-observations",
                "production_mutation_authorized": False}
    if service == "healthy" and api == "healthy" and generation == "healthy":
        return {"classification": "healthy-with-backlog" if backlog else "healthy",
                "next_step": "inspect-queue-capacity" if backlog else "observe",
                "production_mutation_authorized": False}
    if service == "healthy" and api == "unhealthy":
        return {"classification": "api-transient-or-fault",
                "next_step": "bounded-read-only-recheck",
                "production_mutation_authorized": False}
    if generation == "unhealthy":
        return {"classification": "generation-unconfirmed",
                "next_step": "verify-existing-bounded-replay",
                "production_mutation_authorized": False}
    return {"classification": "insufficient-production-evidence",
            "next_step": "obtain-trusted-main-observations",
            "production_mutation_authorized": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", help="path to local JSON evidence; never provide tokens")
    args = parser.parse_args()
    with open(args.evidence, encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        parser.error("evidence must be a JSON object")
    print(json.dumps(classify(value), sort_keys=True))


if __name__ == "__main__":
    main()
