#!/usr/bin/env python3
"""Offline source-only audit of the zCloud production writer serialization boundary.

This report is NOT authority to release, run jobs or infer VPS health.
Only fixed, explicitly scoped producer workflows are examined. Pre-existing
unguarded producer debt is surfaced separately, never reported as protected.
No YAML dependency is required in the trusted GitHub-hosted environment.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

POLICY = "zcloud-production-writer-lock-inventory-v1"
GROUP = "zcloud-production-deploy"

# Each record names a known source writer, not a permission to run it.
# The root entry is the canonical release writer; the others must serialize
# their mutation jobs against its exact non-cancelling Actions concurrency group.
PROTECTED = {
    "zcloud-vps-deploy.yml": ("deploy", "root"),
    "zcloud-postsend-tab-preservation-live.yml": ("install", "job"),
    "zcloud-safe-memory-reclaim-20261013.yml": ("reclaim", "job"),
    "zcloud-v2-activate-addon-in-place.yml": ("activate", "job"),
}

# Already-known collision debt is NOT waived as safe. Changes that introduce
# automatic execution of these legacy jobs must fail the source audit.
KNOWN_DEBT = {
    "zcloud-worker-tab-guard.yml": "install",
    "zcloud-no-browser-churn-hotfix.yml": "install",
    "zcloud-emergency-addon-hot-reload.yml": "activate",
}

WATCHED = (*PROTECTED, *KNOWN_DEBT)


class InvalidSource(ValueError):
    pass


def _only_one_yaml_root(source: str, key: str) -> None:
    if len(re.findall(r"(?m)^" + re.escape(key) + r":\s*$", source)) != 1:
        raise InvalidSource("ambiguous_" + key + "_root")


def _job(source: str, name: str) -> str:
    _only_one_yaml_root(source, "jobs")
    tail = source.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"(?m)^  ([a-z][a-z0-9-]*):\s*$", tail))
    names = [m.group(1) for m in matches]
    if names.count(name) != 1:
        raise InvalidSource("missing_or_duplicate_writer_job")
    index = names.index(name)
    start = matches[index].end()
    end = matches[index + 1].start() if index + 1 < len(matches) else len(tail)
    return tail[start:end]


def _lock(block: str, indent: int) -> str:
    """Return protected, missing, or invalid without inferring runner safety."""
    pad = " " * indent
    spans = list(re.finditer(r"(?m)^" + pad + r"concurrency:\s*$", block))
    if len(spans) != 1:
        return "missing_or_duplicate_lock"
    rest = block[spans[0].end():]
    following = re.search(r"(?m)^" + pad + r"[a-z][a-z0-9-]*:", rest)
    part = rest[:following.start()] if following else rest
    group = re.findall(r"(?m)^" + pad + r"  group:\s*(\S+)\s*$", part)
    cancel = re.findall(r"(?m)^" + pad + r"  cancel-in-progress:\s*(\S+)\s*$", part)
    if group != [GROUP]:
        return "different_writer_lock"
    if cancel != ["false"]:
        return "cancel_can_interrupt_other_writer"
    return "protected"


def inspect_source(name: str, source: str) -> dict[str, str]:
    if name not in WATCHED:
        raise InvalidSource("workflow_not_in_scoped_inventory")
    _only_one_yaml_root(source, "on")
    _only_one_yaml_root(source, "permissions")
    _only_one_yaml_root(source, "jobs")
    required_job, location = PROTECTED.get(name, (KNOWN_DEBT.get(name), "job"))
    if not required_job:
        raise InvalidSource("missing_writer_id")
    writer = _job(source, required_job)
    lock_block = source.split("\nconcurrency:\n", 1)[1].split("\njobs:\n", 1)[0] if "\nconcurrency:\n" in source else ""
    if location == "root":
        outcome = _lock(source.split("\njobs:\n", 1)[0], 0)
    else:
        outcome = _lock(writer, 4)
        # If the root-level workflow lock uses the production group, hosted
        # proof jobs would compete with the live release even when harmless.
        if re.findall(r"(?m)^  group:\s*" + re.escape(GROUP) + r"\s*$", lock_block):
            outcome = "hosted_proof_holds_writer_lock"
    if name in KNOWN_DEBT:
        if "github.event_name == 'workflow_dispatch'" not in writer:
            return {"workflow": name, "job": required_job, "risk": "auto_live_entrypoint_added"}
        # A legacy producer becoming properly serialized is a remediation,
        # not an increase in the admitted debt baseline.
        if outcome == "protected":
            return {"workflow": name, "job": required_job, "risk": "remediated"}
    return {"workflow": name, "job": required_job, "risk": outcome}


def audit(repo_root: Path) -> dict[str, object]:
    directory = repo_root / ".github" / "workflows"
    results = []
    new_debt = []
    for name in WATCHED:
        try:
            source = (directory / name).read_text(encoding="utf-8")
            record = inspect_source(name, source)
        except (OSError, UnicodeError, InvalidSource, IndexError, KeyError):
            record = {"workflow": name, "job": (PROTECTED.get(name) or (KNOWN_DEBT[name],))[0],
                      "risk": "invalid_or_missing_workflow"}
        results.append(record)
        if name in PROTECTED:
            if record["risk"] != "protected":
                new_debt.append(name)
        elif record["risk"] in {"auto_live_entrypoint_added", "invalid_or_missing_workflow"}:
            new_debt.append(name)
    protected = sorted(record["workflow"] for record in results if record["risk"] in {"protected", "remediated"})
    outstanding = sorted(record["workflow"] for record in results
                         if record["risk"] not in {"protected", "remediated"})
    return {
        "policy": POLICY, "source_only": True, "mutation_performed": False,
        "production_verified": False, "release_authorized": False,
        "observed_workflows": len(results), "protected_workflows": protected,
        "outstanding_writer_debt": outstanding,
        "new_or_regressed_debt": sorted(new_debt),
        "findings": sorted(results, key=lambda r: r["workflow"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--deny-new-debt", action="store_true")
    args = parser.parse_args(argv)
    report = audit(args.repo_root)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 2 if args.deny_new_debt and report["new_or_regressed_debt"] else 0


if __name__ == "__main__":
    sys.exit(main())
