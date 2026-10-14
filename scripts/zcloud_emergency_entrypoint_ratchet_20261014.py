#!/usr/bin/env python3
"""Offline fail-closed regression ratchet for retired zCloud emergency authority.

This is a source-only gate. It does not invoke Actions, a VPS, a browser,
SQLite, worker APIs or other live services. Findings contain only bounded
workflow filenames and opaque denial codes; source content stays private.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

WORKFLOW_ROOT = Path(".github/workflows")
FILE_LIMIT = 128 * 1024
POLICY = {
    "zcloud-hosted-emergency-queue-sweep.yml": {
        "job": "observe", "step": "Summarize sampled Actions queue with GET only",
        "confirm": "inputs.confirm_sha == github.sha",
        "approval": None,
    },
    "zcloud-emergency-addon-hot-reload.yml": {
        "job": "activate", "step": "Recheck exact main and idle workers, then reload addon without a Firefox restart",
        "confirm": "inputs.confirm_sha == github.sha",
        "approval": "inputs.approve_addon_reload == true",
    },
    "zcloud-worker-prompt-stop-20261013.yml": {
        "job": "owner-vps-inspection", "step": "Collect redacted numeric summary only",
        "confirm": "inputs.confirm_sha == github.sha",
        "approval": "inputs.approve_readonly_diagnostic == true",
    },
}
TRUST = (
    "github.repository == 'Zennay/zCloud'",
    "github.ref == 'refs/heads/main'",
    "github.actor == 'Zennay'",
    "github.triggering_actor == 'Zennay'",
    "github.run_attempt == 1",
    "github.event_name == 'workflow_dispatch'",
)
SOURCE_TRUST = (
    'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
    'test "$GITHUB_REPOSITORY" = "Zennay/zCloud"',
    'test "$GITHUB_REF" = "refs/heads/main"',
    'test "$GITHUB_ACTOR" = "Zennay"',
    'test "$GITHUB_TRIGGERING_ACTOR" = "Zennay"',
    'test "$GITHUB_RUN_ATTEMPT" = "1"',
)


def inspect_text(filename: str, source: str) -> list[str]:
    if filename not in POLICY:
        return ["unknown_policy_subject"]
    if not isinstance(source, str) or len(source.encode("utf-8")) > FILE_LIMIT:
        return ["invalid_or_oversized_source"]
    problems: list[str] = []
    if source.count("\non:\n") != 1 or source.count("\npermissions:\n") != 1 or source.count("\njobs:\n") != 1:
        return ["ambiguous_document_structure"]
    events = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    triggers = set(re.findall(r"(?m)^  ([a-z_]+):(?:\s|$)", events))
    if triggers != {"pull_request", "workflow_dispatch"}:
        problems.append("automatic_or_unknown_trigger")
    if re.search(r"(?m)^\s*(?:pull_request_target|schedule|push|workflow_run|repository_dispatch|issue_comment):", events):
        problems.append("privileged_trigger")
    permission_block = source.split("\npermissions:\n", 1)[1].split("\n", 1)[1].split("\nconcurrency:", 1)[0].split("\njobs:", 1)[0]
    allowed = {"contents: read", "actions: read"} if filename == "zcloud-hosted-emergency-queue-sweep.yml" else {"contents: read"}
    observed = set(re.findall(r"(?m)^  ([a-z-]+:\s*\w+)\s*$", permission_block))
    if observed != allowed:
        problems.append("token_permissions_not_readonly")
    config = POLICY[filename]
    marker = "\n  " + config["job"] + ":\n"
    if source.count(marker) != 1:
        return sorted(set(problems + ["missing_or_duplicate_job"]))
    job = source.split(marker, 1)[1].split("\n  ", 1)[0] if False else source.split(marker, 1)[1]
    # Trust expression precedes the job's runner. A forged literal in a shell
    # command is not sufficient to satisfy a GitHub job-level admission gate.
    if "    runs-on:" not in job:
        return sorted(set(problems + ["missing_runner"]))
    admission = job.split("    runs-on:", 1)[0]
    for marker in TRUST + (config["confirm"],):
        if marker not in admission:
            problems.append("missing_job_admission")
            break
    if config["approval"] and config["approval"] not in admission:
        problems.append("missing_explicit_optin")
    if filename == "zcloud-hosted-emergency-queue-sweep.yml":
        if "runs-on: ubuntu-latest" not in job or "self-hosted" in job:
            problems.append("privileged_queue_observer_runner")
        forbidden = ('method="POST"', 'method="DELETE"', 'method="PATCH"',
                     'actions: write', 'statuses: write', '/cancel', '/statuses/',
                     "gh api --method", 'git push')
        if any(term in source for term in forbidden):
            problems.append("queue_mutation_authority_reappeared")
        if 'method="GET"' not in job or '"mutation_performed": False' not in job:
            problems.append("missing_readonly_queue_evidence")
    else:
        if "runs-on: [self-hosted, zcloud, vps]" not in job:
            problems.append("missing_canonical_runner")
        if "python3 scripts/zcloud_vps_runner_guard.py --json" not in job:
            problems.append("missing_runner_guard")
    step = "- name: " + config["step"]
    if job.count(step) != 1:
        return sorted(set(problems + ["missing_or_duplicate_live_step"]))
    sensitive = job.split(step, 1)[1]
    if not sensitive.lstrip().startswith("\n") and not sensitive.startswith("\n"):
        problems.append("ambiguous_live_step")
    for marker in SOURCE_TRUST:
        if marker not in sensitive:
            problems.append("missing_in_step_identity_revalidation")
            break
    if ("test \"$(git rev-parse HEAD)\" = \"$GITHUB_SHA\"" not in sensitive or
            "timeout 15 git ls-remote --exit-code origin refs/heads/main" not in sensitive or
            'test "$current_main" = "$GITHUB_SHA"' not in sensitive):
        problems.append("missing_in_step_fresh_main")
    if filename == "zcloud-emergency-addon-hot-reload.yml":
        if 'test "$APPROVE_RELOAD" = "true"' not in sensitive:
            problems.append("missing_in_step_consent")
        command = "node scripts/zcloud_reload_extension.mjs"
    elif filename == "zcloud-worker-prompt-stop-20261013.yml":
        if 'test "$APPROVE_DIAGNOSTIC" = "true"' not in sensitive:
            problems.append("missing_in_step_consent")
        command = "python3 scripts/zcloud_worker_prompt_safe_diagnostic.py"
    else:
        command = 'method="GET"'
    if command not in sensitive:
        problems.append("live_action_or_read_missing")
    else:
        command_start = sensitive.index(command)
        guarded = sensitive[:command_start]
        if filename != "zcloud-hosted-emergency-queue-sweep.yml":
            if "python3 scripts/zcloud_vps_runner_guard.py --json" not in guarded:
                problems.append("live_action_before_runner_guard")
    if "persist-credentials: false" not in source or        "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803" not in source:
        problems.append("checkout_provenance_regressed")
    return sorted(set(problems))


def inspect_repo(root: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for filename in sorted(POLICY):
        path = root / WORKFLOW_ROOT / filename
        if path.is_symlink() or not path.is_file():
            result[filename] = ["missing_or_symlink_workflow"]
            continue
        try:
            if path.stat().st_size > FILE_LIMIT:
                result[filename] = ["oversized_workflow"]
                continue
            result[filename] = inspect_text(filename, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            result[filename] = ["unreadable_workflow"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    options = parser.parse_args(argv)
    results = inspect_repo(options.repo_root)
    problems = {k: v for k, v in results.items() if v}
    print(json.dumps({
        "policy": "zcloud-emergency-entrypoints-source-ratchet-v1",
        "inspected_workflows": len(results), "finding_count": sum(map(len, problems.values())),
        "findings": problems, "mutation_performed": False, "ok": not problems,
        "live_vps_checked": False,
    }, sort_keys=True, separators=(",", ":")))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
