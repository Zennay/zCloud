#!/usr/bin/env python3
"""Offline, conservative PR recovery workflow gate. Advisory only; never deploys."""
import argparse
import json
import re
from pathlib import Path

PR = re.compile(r"(?m)^\s*pull_request(?:_target)?\s*:")
RUNNER = re.compile(r"(?m)^\s*runs-on\s*:\s*(?:\[\s*)?(?:['\"])?self-hosted\b", re.I)
PRIVILEGED = re.compile(r"(?i)\b(?:sudo|systemctl|service\s+\w+\s+(?:restart|stop)|chattr|chmod|chown)\b")

def inspect(text: str) -> dict:
    """Fail closed when untrusted event, runner and privilege cues coexist.

    This is intentionally a static *screen*, not YAML parsing and not proof of
    absence of unsafe paths (called workflows, expressions and aliases exist).
    """
    risks = []
    if PR.search(text) and RUNNER.search(text):
        risks.append("pr_trigger_self_hosted_manual_review")
        if PRIVILEGED.search(text):
            risks.append("pr_trigger_self_hosted_privileged_cues")
    if "pull_request_target:" in text and RUNNER.search(text):
        risks.append("pr_target_self_hosted_requires_manual_review")
    return {
        "review_required": bool(risks),
        "signals": sorted(set(risks)),
        "deploy_authorized": False,
        "recovery_authorized": False,
        "mutation_performed": False,
        "static_screen_only": True,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path)
    args = parser.parse_args()
    try:
        path = args.workflow
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError("unsafe or oversized input")
        result = inspect(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        result = {"review_required": True, "signals": ["invalid_input"],
                  "deploy_authorized": False, "recovery_authorized": False,
                  "mutation_performed": False, "static_screen_only": True}
    print(json.dumps(result, sort_keys=True))
    return 2 if result["review_required"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
