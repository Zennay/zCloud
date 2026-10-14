#!/usr/bin/env python3
"""Opt-in GitHub-hosted PR integration engine for zCloud development.

No VPS access, runner restart, queue write, secrets output or bypass of GitHub
branch protections. An explicit label is a per-PR authorization, not a blanket
instruction to merge every open branch. Default mode is dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = "Zennay/zCloud"
LABEL = "zcloud-automerge-ready"
SHA = re.compile(r"^[a-f0-9]{40}$")
HIGH_RISK = (
    ".github/", "public/", "firefox-extension/", "server.py",
    "projects.json", "project-contracts.json", "lane_generator.py",
    "deploy/", "worker/", "config/", "systemd/",
)
ALLOWED_PREFIXES = ("docs/", "tests/", "scripts/")
# GitHub-hosted integration has a write-scoped token. Never use that same
# token to auto-merge changes to its own authorization, admission evidence or
# operator reporting logic, even when an owner adds the usual approval label.
# These exact files require a separately reviewed, manually merged PR.
PROTECTED_INTEGRATION_FILES = frozenset((
    "scripts/zcloud_autonomous_pr_integrator.py",
    "scripts/zcloud_automerge_report_20261014.py",
    "tests/test_zcloud_autonomous_pr_integrator.py",
    "tests/test_zcloud_automerge_report_20261014.py",
    "tests/test_deployops_automerge_workflow_replay_20261014_w11.py",
    "tests/test_deployops_automerge_trust_roots_20261014_w12.py",
    "tests/test_deployops_automerge_check_provenance_20261014_w14.py",
    "tests/test_deployops_automerge_file_count_cpu_20261014_w15.py",
    "tests/test_deployops_automerge_import_shadow_20261014_w16.py",
))
# These Python module-loading entrypoints are reachable from the trusted
# GitHub-hosted unittest preflight, even when the integrator engine itself is
# unchanged. Never allow an automatically merged PR to edit them.
PYTHON_PACKAGE_ENTRYPOINTS = frozenset((
    "scripts/__init__.py", "tests/__init__.py",
    "scripts/sitecustomize.py", "scripts/usercustomize.py",
    "tests/sitecustomize.py", "tests/usercustomize.py",
))
STDLIB_IMPORT_ROOTS = frozenset(sys.stdlib_module_names)


def unsafe_import_shadow(path):
    if not isinstance(path, str):
        return False
    if path in PYTHON_PACKAGE_ENTRYPOINTS:
        return True
    if not path.startswith(("scripts/", "tests/")):
        return False
    if path.endswith((".pyc", ".pyo")):
        return True
    if path.startswith("scripts/"):
        # python3 scripts/zcloud_autonomous_pr_integrator.py puts scripts/
        # first on sys.path. json.py, argparse.py, urllib/__init__.py, etc.
        # can shadow imports before the integrity guard itself starts.
        component = path.split("/", 2)[1]
        module_name = component[:-3] if component.endswith(".py") else component
        if module_name in STDLIB_IMPORT_ROOTS:
            return True
    return False


ALLOWED_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}
# Verified against the actual GitHub Actions check-run API on recent zCloud PR
# heads. A human-supplied name alone cannot attest the critical regression job.
GITHUB_ACTIONS_APP_ID = 15368
TRUSTED_JOB_URL = re.compile(
    r"^https://github[.]com/Zennay/zCloud/actions/runs/([1-9][0-9]*)/job/([1-9][0-9]*)$"
)
REQUIRED_ADMISSION = "Reject untrusted regression events before checkout"


def trusted_job_identity(check):
    """Bind trusted GitHub-hosted job ID and check suite; never authorize a run."""
    if not isinstance(check, dict):
        return None
    app = check.get("app")
    owner = app.get("owner") if isinstance(app, dict) else None
    suite = check.get("check_suite")
    url = check.get("details_url")
    match = TRUSTED_JOB_URL.fullmatch(url) if isinstance(url, str) else None
    if (not isinstance(app, dict) or app.get("slug") != "github-actions"
            or app.get("id") != GITHUB_ACTIONS_APP_ID
            or not isinstance(owner, dict) or owner.get("login") != "github"
            or not isinstance(suite, dict)
            or type(suite.get("id")) is not int or suite["id"] <= 0
            or match is None
            or type(check.get("id")) is not int
            or check["id"] != int(match.group(2))
            or check.get("html_url") != url):
        return None
    return int(match.group(1)), suite["id"]




class Refused(RuntimeError):
    pass


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def safe_path(path):
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    components = path.split("/")
    if any(c in ("", ".", "..") for c in components):
        return False
    if path in PROTECTED_INTEGRATION_FILES:
        return False
    if unsafe_import_shadow(path):
        return False
    if path.startswith(HIGH_RISK) or path in HIGH_RISK:
        return False
    return path.startswith(ALLOWED_PREFIXES)


def has_label(pr):
    return any(isinstance(item, dict) and item.get("name") == LABEL
               for item in pr.get("labels", []))


def decide(pr, files, checks, statuses, main_sha):
    """Pure fail-closed admission. Return reason, never permission from text."""
    if not isinstance(pr, dict) or not valid_sha(main_sha):
        return "invalid_input"
    if not has_label(pr):
        return "no_explicit_label"
    if pr.get("state") != "open" or pr.get("draft") is not False:
        return "not_review_ready"
    if pr.get("mergeable") is not True or pr.get("mergeable_state") not in ("clean", "unstable"):
        return "not_cleanly_mergeable"
    head, base = pr.get("head"), pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return "missing_refs"
    head_sha = head.get("sha")
    if not valid_sha(head_sha) or not valid_sha(base.get("sha")):
        return "invalid_sha"
    if base.get("sha") != main_sha or base.get("ref") != "main":
        return "base_drift"
    head_repo = (head.get("repo") or {}).get("full_name")
    base_repo = (base.get("repo") or {}).get("full_name")
    if head_repo != REPO or base_repo != REPO or head_sha == main_sha:
        return "foreign_or_empty_head"
    if not isinstance(files, list) or not (1 <= len(files) < 100):
        return "incomplete_file_inventory"
    filenames = set()
    for f in files:
        if not isinstance(f, dict) or f.get("status") not in ("added", "modified"):
            return "unsupported_file_change"
        name = f.get("filename")
        if name in PROTECTED_INTEGRATION_FILES:
            return "protected_integration_source"
        if unsafe_import_shadow(name):
            return "unsafe_python_import_root"
        if not safe_path(name) or name in filenames:
            return "unsafe_or_duplicate_path"
        filenames.add(name)
    # GitHub's single-page /pulls/:number/files API has no total_count.
    # Bind the fetched set to the authoritative /pulls/:number changed_files
    # field, both on first admission and the pre-merge second read.
    file_count = pr.get("changed_files")
    if type(file_count) is not int or not 1 <= file_count < 100 or file_count != len(filenames):
        return "incomplete_file_inventory"
    if not isinstance(checks, list) or not (1 <= len(checks) < 100):
        return "missing_or_unbounded_checks"
    if not isinstance(statuses, list) or len(statuses) >= 100:
        return "unbounded_statuses"
    smoke_green = False
    smoke_identity = None
    admission_identity = None
    optional_cpu_pending = False
    for c in checks:
        if not isinstance(c, dict) or c.get("head_sha") != head_sha:
            return "unbound_check"
        name = c.get("name")
        if name == "critical-runner-flows":
            if smoke_green:
                return "duplicate_required_check"
            if c.get("status") != "completed" or c.get("conclusion") != "success":
                return "required_smoke_not_green"
            smoke_identity = trusted_job_identity(c)
            if smoke_identity is None:
                return "untrusted_required_check"
            smoke_green = True
        elif name == REQUIRED_ADMISSION:
            if admission_identity is not None:
                return "duplicate_required_check"
            if c.get("status") != "completed" or c.get("conclusion") != "success":
                return "required_admission_not_green"
            admission_identity = trusted_job_identity(c)
            if admission_identity is None:
                return "untrusted_required_check"
        elif name == "cpu-snapshot" and c.get("status") in ("queued", "in_progress", "pending") and c.get("conclusion") is None:
            # A same-named third-party check must not manufacture an
            # exception to the GitHub mergeable_state=unstable policy.
            # Assignment/queue state is NOT proof that the VPS is healthy.
            if trusted_job_identity(c) is None:
                return "untrusted_optional_cpu_check"
            optional_cpu_pending = True
            continue
        elif c.get("status") != "completed" or c.get("conclusion") not in ALLOWED_CHECK_CONCLUSIONS:
            return "check_not_green"
    if not smoke_green:
        return "required_smoke_missing"
    if admission_identity is None:
        return "required_admission_missing"
    if smoke_identity != admission_identity:
        return "unbound_admission_suite"
    # GitHub reports unstable when ANY status check is pending; the single
    # recognized optional VPS CPU snapshot may be queued indefinitely.
    # Never relax other required/unknown checks or a blocked branch-protection
    # state: the GitHub merge endpoint enforces protections again.
    if pr.get("mergeable_state") == "unstable" and not optional_cpu_pending:
        return "unstable_without_known_optional"
    for s in statuses:
        if not isinstance(s, dict) or s.get("state") != "success":
            return "status_not_green"
    # Source integration is independent of the VPS deployment health status.
    # Privileged/runtime paths are excluded above; a merge is never permission
    # to restart a worker, alter SQLite or promote production.
    return "eligible"


def api(token, method, path, data=None):
    url = "https://api.github.com/repos/" + REPO + path
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "zcloud-dev-automerge/1",
               "Authorization": "Bearer " + token,
               "X-GitHub-Api-Version": "2022-11-28"}
    payload = json.dumps(data, separators=(",", ":")).encode() if data is not None else None
    req = Request(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise Refused("api_response_oversize")
            return json.loads(body)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise Refused("github_api_request_failed") from exc



def list_open_prs(transport, token, max_pages=10):
    """Use bounded GitHub pages; never silently ignore older opted-in PRs."""
    seen = set()
    prs = []
    for page in range(1, max_pages + 1):
        batch = transport(token, "GET",
                          f"/pulls?state=open&sort=updated&direction=desc&per_page=100&page={page}")
        if not isinstance(batch, list) or len(batch) > 100:
            raise Refused("invalid_pr_page")
        for pr in batch:
            number = pr.get("number") if isinstance(pr, dict) else None
            if type(number) is not int or not 1 <= number <= 10000000 or number in seen:
                raise Refused("ambiguous_pr_page")
            seen.add(number)
            prs.append(pr)
        if len(batch) < 100:
            return prs
    raise Refused("pr_inventory_exceeds_bounded_budget")


def main(argv=None, environ=None, transport=api):
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Merge exactly one eligible labelled PR")
    options = parser.parse_args(argv)
    if env.get("GITHUB_REPOSITORY") != REPO or env.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("not_canonical_main")
    if env.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch"):
        raise Refused("untrusted_event")
    # Rerunning an already-consumed GitHub workflow run must never admit a
    # second PR merge. GitHub's per-run job lock is not a durable replay gate:
    # an operator can rerun the same event after its first job terminates.
    if env.get("GITHUB_RUN_ATTEMPT") != "1":
        raise Refused("replayed_or_unknown_attempt")
    if options.apply and env.get("ZCLOUD_AUTOMERGE_ENABLED") != "true":
        raise Refused("explicit_feature_gate_disabled")
    if options.apply and (env.get("GITHUB_ACTOR") != "Zennay"
                          or env.get("GITHUB_TRIGGERING_ACTOR") != "Zennay"):
        raise Refused("owner_only_apply")
    token = env.get("GITHUB_TOKEN", "")
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token) or len(token) > 1024:
        raise Refused("missing_or_invalid_token")
    checked_sha = env.get("GITHUB_SHA")
    if not valid_sha(checked_sha):
        raise Refused("invalid_trigger_sha")
    branch = transport(token, "GET", "/branches/main")
    main_sha = (branch.get("commit") or {}).get("sha")
    if main_sha != checked_sha:
        raise Refused("workflow_source_is_stale")
    prs = list_open_prs(transport, token)
    receipts = []
    for p in prs:
        if not isinstance(p, dict) or not has_label(p):
            continue
        number = p.get("number")
        if type(number) is not int or not 1 <= number <= 10000000:
            raise Refused("invalid_pr_number")
        pr = transport(token, "GET", f"/pulls/{number}")
        head_sha = (pr.get("head") or {}).get("sha")
        if not valid_sha(head_sha):
            receipts.append({"pr": number, "decision": "invalid_sha"})
            continue
        files = transport(token, "GET", f"/pulls/{number}/files?per_page=100")
        checks = transport(token, "GET", f"/commits/{head_sha}/check-runs?per_page=100")
        statuses = transport(token, "GET", f"/commits/{head_sha}/statuses?per_page=100")
        if not isinstance(checks, dict) or checks.get("total_count") != len(checks.get("check_runs", [])):
            verdict = "incomplete_checks"
        else:
            verdict = decide(pr, files, checks["check_runs"], statuses, main_sha)
        receipts.append({"pr": number, "decision": verdict})
        if verdict != "eligible" or not options.apply:
            continue
        latest_main = (transport(token, "GET", "/branches/main").get("commit") or {}).get("sha")
        latest_pr = transport(token, "GET", f"/pulls/{number}")
        if (latest_main != main_sha
                or (latest_pr.get("head") or {}).get("sha") != head_sha
                or (latest_pr.get("base") or {}).get("sha") != main_sha
                or not has_label(latest_pr)):
            receipts[-1]["decision"] = "changed_before_merge"
            continue
        # Admission is not a one-time snapshot: a PR can become draft, gain a
        # blocking review, or have its exact-SHA checks rerun between our first
        # read and the write. Re-fetch all trusted admission facts immediately
        # before the SHA-bound merge (which still enforces branch protection).
        fresh_files = transport(token, "GET", f"/pulls/{number}/files?per_page=100")
        fresh_checks = transport(token, "GET", f"/commits/{head_sha}/check-runs?per_page=100")
        fresh_statuses = transport(token, "GET", f"/commits/{head_sha}/statuses?per_page=100")
        if (not isinstance(fresh_checks, dict)
                or type(fresh_checks.get("total_count")) is not int
                or fresh_checks["total_count"] != len(fresh_checks.get("check_runs", []))):
            receipts[-1]["decision"] = "recheck_incomplete_checks"
            continue
        fresh_verdict = decide(latest_pr, fresh_files, fresh_checks["check_runs"],
                               fresh_statuses, latest_main)
        if fresh_verdict != "eligible":
            receipts[-1]["decision"] = "recheck_" + fresh_verdict
            continue
        # Source integrations must not implicitly attempt production promotion.
        # An explicitly approved production release runs through its own gate.
        title = str(pr.get("title") or "Development integration")
        title = "".join(c if 32 <= ord(c) <= 126 and c != chr(92) else " " for c in title)[:120].strip()
        result = transport(token, "PUT", f"/pulls/{number}/merge",
                           {"sha": head_sha, "merge_method": "squash",
                            "commit_title": title + " [skip ci]"})
        if result.get("merged") is not True or not valid_sha(result.get("sha")):
            raise Refused("merge_not_confirmed")
        receipts[-1]["decision"] = "merged"
        receipts[-1]["merge_sha"] = result["sha"]
        break  # Deliberately only one merge per run; next run revalidates main.
    # Preserve a bounded, metadata-only backlog inventory. These counts are
    # visibility, NOT automatic review or a substitute for per-PR authorization.
    # PR titles, authors, branch names and arbitrary user content are omitted.
    inventory = {
        "open_prs_scanned": len(prs),
        "explicitly_labeled": sum(has_label(p) for p in prs),
        "draft_prs": sum(p.get("draft") is True for p in prs),
        "unlabeled_non_draft": sum(not has_label(p) and p.get("draft") is False
                                   for p in prs),
        "review_only_pr_ids": [
            p["number"] for p in prs
            if not has_label(p) and p.get("draft") is False
        ][:10],
    }
    print(json.dumps({"mode": "apply" if options.apply else "dry_run",
                      "repo": REPO, "inventory": inventory,
                      "receipts": receipts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as exc:
        print(json.dumps({"state": "refused", "reason": str(exc)}))
        sys.exit(2)
