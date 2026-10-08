#!/usr/bin/env python3
"""Conservative, offline heuristic for workflow_run -> privileged self-hosted jobs.

No YAML dependency, no subprocess, no network access. This is a screening hint,
NOT a security proof or deploy authorization. Ambiguity fails closed.
"""
import json
import re
import sys
from pathlib import Path

def screen(text):
    if not isinstance(text, str) or not text.strip():
        return {"verdict": "UNKNOWN_DENY", "reason": "empty_input",
                "deploy_authorized": False, "recovery_authorized": False,
                "mutation_performed": False}
    trigger = bool(re.search(r"(?m)^\s*(?:on:|['\"]on['\"]:)\s*(?:\n(?:\s+[^\n]+\n)*?\s+workflow_run\s*:|\{[^\n}]*workflow_run\s*:|\[?workflow_run\b)", text))
    privileged = bool(re.search(r"(?im)\b(?:sudo|systemctl|service|chown|chmod|chattr|docker|kubectl|git\s+push)\b", text))
    self_hosted = bool(re.search(r"(?i)self-hosted", text))
    write_permission = bool(re.search(r"(?im)^\s*(?:contents|actions|deployments|packages|id-token)\s*:\s*write\s*$", text))
    if trigger and (self_hosted or privileged or write_permission):
        verdict, reason = "REVIEW_REQUIRED", "workflow_run_with_sensitive_execution_cues"
    elif not trigger:
        verdict, reason = "UNKNOWN_DENY", "workflow_run_not_confidently_identified"
    else:
        verdict, reason = "UNKNOWN_DENY", "heuristic_cannot_establish_safe_execution"
    return {"verdict": verdict, "reason": reason, "deploy_authorized": False,
            "recovery_authorized": False, "mutation_performed": False}

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        result = screen(None)
    else:
        try:
            path = Path(argv[0])
            result = screen(path.read_text(encoding="utf-8")) if path.is_file() and path.stat().st_size <= 1024 * 1024 else screen(None)
        except (OSError, UnicodeError, ValueError):
            result = screen(None)
    print(json.dumps(result, sort_keys=True))
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
