"""Offline, non-authorizing HTTP Content-Type screen for deploy evidence.

A passing media type is only a syntactic observation. It is never sufficient
to authenticate evidence, fetch an artifact, release a gate, or deploy.
"""
import re

_ALLOWED = re.compile(r"application/json(?:;[ \t]*charset=utf-8)?\Z", re.ASCII)


def screen_content_type(value):
    """Return a denial-safe observation; never grant any mutation authority."""
    valid = (
        isinstance(value, str)
        and len(value) <= 128
        and _ALLOWED.fullmatch(value) is not None
    )
    return {
        "observation": "SYNTAX_ONLY_UNVERIFIED" if valid else "REJECTED",
        "deploy_authorized": False,
        "recovery_authorized": False,
        "mutation_authorized": False,
    }


if __name__ == "__main__":
    import sys
    print(screen_content_type(sys.argv[1] if len(sys.argv) == 2 else None))
    sys.exit(2)  # Must not accidentally become a green release gate.
