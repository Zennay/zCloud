#!/usr/bin/env python3
"""Offline, deny-only screen for purported zCloud deployment runner labels.

Not a GitHub runner identity check, live attestation, or deployment admission gate.
"""
import argparse
import json

REQUIRED = frozenset(("self-hosted", "zcloud", "vps"))

def screen(labels):
    valid = (
        isinstance(labels, list)
        and len(labels) == len(REQUIRED)
        and all(isinstance(item, str) and item == item.strip() for item in labels)
        and len(set(labels)) == len(labels)
        and set(labels) == REQUIRED
    )
    return {
        "classification": "SYNTAX_ONLY_UNVERIFIED" if valid else "INVALID_LABEL_SET",
        "runner_identity_verified": False,
        "release_authorized": False,
        "deploy_authorized": False,
        "recovery_authorized": False,
        "mutation_performed": False,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels_json", help="JSON representation of purported labels")
    args = parser.parse_args()
    try:
        labels = json.loads(args.labels_json)
    except (ValueError, TypeError):
        labels = None
    print(json.dumps(screen(labels), sort_keys=True))
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
