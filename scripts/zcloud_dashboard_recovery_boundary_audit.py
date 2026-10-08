#!/usr/bin/env python3
"""Static, non-authorizing inventory for the dashboard recovery writer boundary."""
from pathlib import Path
import json
import re

WORKFLOW = Path(".github/workflows/zcloud-dashboard-access-recovery.yml")
PATTERNS = {
    "service_mutation": r"\bsystemctl\s+(?:stop|start|restart|reset-failed)\b",
    "filesystem_mutation": r"\b(?:chown|chmod|chattr)\s+",
    "sqlite_write_probe": r"BEGIN IMMEDIATE",
    "untrusted_pr_trigger": r"(?m)^\s*pull_request:\s*$",
}
def inspect(text: str) -> dict:
    observed = {key: bool(re.search(pattern, text)) for key, pattern in PATTERNS.items()}
    return {
        "schema": "zcloud-dashboard-recovery-boundary-v1",
        "observed": observed,
        "requires_serialized_owner": any(observed[k] for k in ("service_mutation", "filesystem_mutation", "sqlite_write_probe")),
        "safe_for_parallel_dispatch": False,
        "mutation_performed": False,
        "deploy_authorized": False,
        "workflow_dispatch_authorized": False,
    }

def main() -> None:
    result = inspect(WORKFLOW.read_text(encoding="utf-8"))
    print(json.dumps(result, sort_keys=True))
    if not result["requires_serialized_owner"]:
        raise SystemExit("Fail closed: recovery writer boundary not detected")

if __name__ == "__main__":
    main()
