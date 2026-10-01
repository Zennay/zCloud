#!/usr/bin/env python3
"""Claim a zSSH runtime release through the local zCloud coordination gate."""

import argparse
import http.client
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
REPO = "Zennay/zSSH"
PROJECT = "zssh"
WORKER = "zssh::w1"
CLAIM = "m1-live-file-proof-and-standalone-release"
PROJECT_REF = "https://app.notion.com/p/3e89e19ac955811a9008d420e3e2a634"
HANDOFF_REF = "https://app.notion.com/p/3e89e19ac95581639bdcdc9daeb37ae8"

_TRANSIENT_HTTP = {502, 503, 504}


def _read_error_body(error):
    try:
        return error.read().decode("utf-8", "replace")[:1000]
    except Exception:
        return ""


def get_json(url, *, attempts=3, delay_seconds=1):
    """GET JSON with narrow retries for transient control-plane failures."""
    request = urllib.request.Request(url, headers={"User-Agent": "zssh-release-runner"})
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = _read_error_body(error)
            if error.code not in _TRANSIENT_HTTP or attempt + 1 >= attempts:
                raise RuntimeError(
                    f"GET {url} rejected request ({error.code}): {detail}"
                ) from error
            last_error = error
        except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError) as error:
            if attempt + 1 >= attempts:
                raise RuntimeError(
                    f"GET {url} transport failed after {attempts} attempt(s): {error}"
                ) from error
            last_error = error
        time.sleep(delay_seconds * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last_error}")


def post_json(endpoint, payload, *, allow_conflict=False):
    request = urllib.request.Request(
        BASE + endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = _read_error_body(error)
        if allow_conflict and error.code == 409:
            try:
                parsed = json.loads(detail or "{}")
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("released") is False:
                return parsed
        raise RuntimeError(f"zCloud {endpoint} rejected request ({error.code}): {detail}") from error


def owner():
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdecimal():
        raise RuntimeError("GITHUB_RUN_ID is required")
    return f"github-actions:zssh:{run_id}"


def acquire(expected_sha):
    if len(expected_sha) != 40 or any(c not in "0123456789abcdef" for c in expected_sha):
        raise RuntimeError("expected SHA must be a full lowercase Git commit ID")

    github = "https://api.github.com/repos/" + REPO
    branch = get_json(github + "/branches/main")
    actual_sha = branch["commit"]["sha"]
    if actual_sha != expected_sha:
        raise RuntimeError(f"zSSH main changed: expected {expected_sha}, got {actual_sha}")
    prs = get_json(github + "/pulls?state=open&per_page=100")
    branches = get_json(github + "/branches?per_page=100")

    # zCloud performs its own current claim-set and VPS-health checks atomically.
    get_json(BASE + "/api/task-claims?project=zssh")
    get_json(BASE + "/api/status")
    preflight = post_json("/api/worker-preflight", {
        "project_id": PROJECT,
        "worker_id": WORKER,
        "owner_id": owner(),
        "vps_profile": "control_plane",
        "notion": {"checked": True, "project_ref": PROJECT_REF, "handoff_ref": HANDOFF_REF},
        "github": {
            "checked": True, "repo": REPO, "main_sha": actual_sha,
            "open_prs": [str(item["number"]) for item in prs],
            "branches": [item["name"] for item in branches],
        },
    })
    if not preflight.get("ok"):
        raise RuntimeError(f"coordination preflight blocked: {preflight.get('blocked')}")

    claim = post_json("/api/task-claims", {
        "action": "acquire", "project_id": PROJECT, "worker_id": WORKER,
        "owner_id": owner(), "claim_key": CLAIM, "lease_seconds": 900,
        "metadata": {
            "task": "Promote standalone zSSH and prove live file capabilities",
            "branch": "main", "sha": expected_sha,
            "conflict_scope": {
                "capabilities": ["zssh-runtime-deploy"],
                "files": ["deploy/install-live.sh", "live-canary.mjs"],
            },
        },
    })
    if not claim.get("acquired"):
        raise RuntimeError(f"zSSH release claim blocked: {claim.get('blocked')}")
    print(f"ZSSH_RELEASE_CLAIMED sha={expected_sha} owner={owner()}")


def release():
    # The cleanup step runs with if: always(). If preflight failed before a
    # claim existed, the API returns 409 {"released": false}; that is a safe,
    # idempotent cleanup result and must not hide the original failure.
    result = post_json("/api/task-claims", {
        "action": "release", "project_id": PROJECT,
        "claim_key": CLAIM, "owner_id": owner(),
    }, allow_conflict=True)
    print(f"ZSSH_RELEASE_CLAIM_RELEASED released={result.get('released', False)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["acquire", "release"])
    parser.add_argument("--sha", default="")
    args = parser.parse_args()
    if args.action == "acquire":
        acquire(args.sha)
    else:
        release()
