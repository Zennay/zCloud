#!/usr/bin/env python3
"""Offline, non-authorizing deploy evidence artifact path boundary.

This deliberately cannot authorize deployment, recovery, merging or mutation.
It only rejects dangerous *relative POSIX artifact member names* before a
separate trusted component considers extracting evidence. It never extracts.
"""
from __future__ import annotations

import json
import re
import sys

_DRIVE = re.compile(r"^[a-zA-Z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def evaluate_member(name: object) -> dict[str, object]:
    reasons: list[str] = []
    if not isinstance(name, str) or not name:
        reasons.append("missing_or_nonstring")
    else:
        if len(name.encode("utf-8")) > 512:
            reasons.append("overlong")
        if _CONTROL.search(name):
            reasons.append("control_character")
        if name.startswith(("/", "\\\\")) or _DRIVE.match(name):
            reasons.append("absolute_path")
        if "\\" in name:
            reasons.append("backslash")
        if any(part in ("", ".", "..") for part in name.split("/")):
            reasons.append("unsafe_component")
        if ":" in name:
            reasons.append("colon")
        if any(not _SAFE_PART.fullmatch(part) for part in name.split("/")):
            reasons.append("unsupported_character")
        if name.endswith((" ", ".")):
            reasons.append("ambiguous_suffix")
    return {
        "accepted_for_offline_name_screen": not reasons,
        "reasons": sorted(set(reasons)),
        "deploy_authorized": False,
        "recovery_authorized": False,
        "mutation_performed": False,
    }


def main() -> int:
    # The CLI intentionally does not echo untrusted names (which may contain
    # terminal escape sequences or sensitive strings).
    result = evaluate_member(sys.argv[1] if len(sys.argv) == 2 else None)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["accepted_for_offline_name_screen"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
