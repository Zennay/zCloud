"""Offline, non-authorizing admission screen for a PR-triggered privileged job.

This intentionally does not parse arbitrary YAML and must never authorize a run.
It only detects a narrowly specified unsafe source pattern in the dashboard
recovery workflow. Unknown shapes are reported as needing owner review.
"""
from pathlib import Path
import re

WORKFLOW = Path(".github/workflows/zcloud-dashboard-access-recovery.yml")


def recovery_pr_risk(workflow_text: str) -> str:
    """Return 'quarantined', 'exposed', or 'review' (never an allow decision)."""
    if not isinstance(workflow_text, str):
        return "review"
    # Anchored layout from this project's workflow; fail closed on drift.
    if not re.search(r"(?m)^  pull_request:\s*$", workflow_text):
        return "review"
    block = re.search(r"(?ms)^  recover:\s*\n(.*?)(?=^  [A-Za-z][\w-]*:\s*$|\Z)", workflow_text)
    if not block:
        return "review"
    body = block.group(1)
    if not re.search(r"(?m)^    runs-on:\s*self-hosted\s*$", body):
        return "review"
    if re.search(r"(?m)^    if:\s*\$\{\{\s*false\s*\}\}\s*$", body):
        return "quarantined"
    # This is a warning, not a judgment that alternative policy is impossible.
    return "exposed"


if __name__ == "__main__":
    import sys
    verdict = recovery_pr_risk(WORKFLOW.read_text(encoding="utf-8"))
    print(f"dashboard_recovery_pr_risk={verdict}")
    sys.exit(0 if verdict == "quarantined" else 2)
