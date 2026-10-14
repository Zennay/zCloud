"""Offline syntax-only artifact SHA-256 digest screen.

This module does not fetch artifacts, verify content, approve a deployment,
or exercise any privileged workflow. A syntactically valid digest is never
sufficient evidence of artifact integrity or release authority.
"""
from __future__ import annotations

import argparse
import json
import re

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)


def screen_digest(value: object) -> dict[str, object]:
    """Return a fail-closed non-authorizing assessment of a digest string."""
    valid = isinstance(value, str) and _SHA256.fullmatch(value) is not None
    return {
        "status": "SYNTAX_ONLY_UNVERIFIED" if valid else "REJECTED",
        "syntax_valid": valid,
        "content_verified": False,
        "deploy_authorized": False,
        "recovery_authorized": False,
        "mutation_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", help="literal sha256:<64 lowercase hex characters>")
    args = parser.parse_args()
    print(json.dumps(screen_digest(args.digest), sort_keys=True))
    # Intentionally nonzero even on valid syntax: cannot act as an admission gate.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
