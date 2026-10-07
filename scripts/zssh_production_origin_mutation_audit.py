"""Read-only audit for zSSH production-origin mutation-semantics claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SCHEMA_VERSION = 1
MAX_WORKFLOW_BYTES = 256 * 1024
MUTATING_REVIEWER_HELPER = "deploy/prepare-reviewer-target.sh"


def audit_workflow_text(text: str) -> dict[str, object]:
    if type(text) is not str:
        raise TypeError("workflow text must be a string")
    if len(text.encode("utf-8")) > MAX_WORKFLOW_BYTES:
        raise ValueError("workflow text exceeds audit bound")

    helper_invoked = MUTATING_REVIEWER_HELPER in text
    declares_no_public_gateway_mutation = '"public_gateway_state_mutated": False' in text
    runtime_receipt_write = "scripts/zcloud_runtime.py" in text and "receipt" in text
    github_status_write = "statuses: write" in text and "/statuses/" in text

    reasons: list[str] = []
    if helper_invoked and declares_no_public_gateway_mutation:
        reasons.append("mutating_reviewer_helper_with_false_claim")
    if runtime_receipt_write:
        reasons.append("runtime_receipt_write_present")
    if github_status_write:
        reasons.append("github_status_write_present")

    truthful = "mutating_reviewer_helper_with_false_claim" not in reasons
    return {
        "schema_version": SCHEMA_VERSION,
        "truthful_public_gateway_mutation_claim": truthful,
        "known_mutating_reviewer_helper_invoked": helper_invoked,
        "declares_no_public_gateway_mutation": declares_no_public_gateway_mutation,
        "runtime_receipt_write_present": runtime_receipt_write,
        "github_status_write_present": github_status_write,
        "reason_codes": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--require-truthful", action="store_true")
    args = parser.parse_args()

    path = Path(args.workflow)
    data = path.read_bytes()
    if len(data) > MAX_WORKFLOW_BYTES:
        raise SystemExit("workflow exceeds audit bound")
    result = audit_workflow_text(data.decode("utf-8"))
    print(json.dumps(result, sort_keys=True))
    if args.require_truthful and not result["truthful_public_gateway_mutation_claim"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
