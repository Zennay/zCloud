#!/usr/bin/env python3
"""Coordinate and verify the permanent zCloud VPS-first execution lane."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
REPO = "Zennay/zCloud"
PROJECT = "cloud"
WORKER = "cloud::w1"
CLAIM = "cloud-permanent-vps-first-deploylane"
PROJECT_REF = "https://app.notion.com/p/3e79e19ac955811d8fd4d35d176bdeb8"
HANDOFF_REF = "https://app.notion.com/p/3e79e19ac955819e9ccee5bec98bbb9c"


def owner() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdecimal():
        raise RuntimeError("GITHUB_RUN_ID is required")
    return f"github-actions:zcloud-deploylane:{run_id}"


def _headers() -> dict[str, str]:
    headers = {"User-Agent": "zcloud-vps-deploylane"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def get_json(url: str) -> object:
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def post_json(endpoint: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "zcloud-vps-deploylane"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"zCloud {endpoint} rejected request ({error.code}): {detail}") from error


def current_claims() -> list[dict]:
    payload = get_json(BASE + "/api/task-claims?project=" + PROJECT)
    if isinstance(payload, dict):
        claims = payload.get("claims", [])
    else:
        claims = []
    return claims if isinstance(claims, list) else []


def exact_claim() -> dict | None:
    expected_owner = owner()
    for claim in current_claims():
        if (
            claim.get("project_id") == PROJECT
            and claim.get("claim_key") == CLAIM
            and claim.get("owner_id") == expected_owner
            and claim.get("worker_id") == WORKER
        ):
            return claim
    return None


def acquire() -> None:
    # Revalidate the local control plane and current claim set before a new claim.
    get_json(BASE + "/api/status")
    current_claims()

    github = "https://api.github.com/repos/" + REPO
    branch = get_json(github + "/branches/main")
    main_sha = branch["commit"]["sha"]
    prs = get_json(github + "/pulls?state=open&per_page=100")
    branches = get_json(github + "/branches?per_page=100")

    preflight = post_json("/api/worker-preflight", {
        "project_id": PROJECT,
        "worker_id": WORKER,
        "owner_id": owner(),
        "notion": {
            "checked": True,
            "project_ref": PROJECT_REF,
            "handoff_ref": HANDOFF_REF,
        },
        "github": {
            "checked": True,
            "repo": REPO,
            "main_sha": main_sha,
            "open_prs": [str(item["number"]) for item in prs],
            "branches": [item["name"] for item in branches],
        },
    })
    if not preflight.get("ok"):
        raise RuntimeError(f"coordination preflight blocked: {preflight.get('blocked')}")

    claim = post_json("/api/task-claims", {
        "action": "acquire",
        "project_id": PROJECT,
        "worker_id": WORKER,
        "owner_id": owner(),
        "claim_key": CLAIM,
        "lease_seconds": 900,
        "metadata": {
            "task": "Permanent VPS-first deploylane without temporary HaxLab bridge",
            "repo": REPO,
            "workflow": "zcloud-vps-execution-probe.yml",
            "conflict_scope": {
                "capabilities": ["zcloud-vps-execution", "zcloud-production-deploy"],
                "files": [
                    ".github/workflows/zcloud-vps-execution-probe.yml",
                    ".github/workflows/zcloud-vps-deploy.yml",
                    "scripts/zcloud_vps_execution_probe.sh",
                ],
            },
        },
    })
    if not claim.get("acquired"):
        raise RuntimeError(f"deploylane claim blocked: {claim.get('blocked')}")

    heartbeat = post_json("/api/task-claims", {
        "action": "heartbeat",
        "project_id": PROJECT,
        "claim_key": CLAIM,
        "owner_id": owner(),
        "lease_seconds": 900,
    })
    if not heartbeat.get("renewed"):
        raise RuntimeError("deploylane claim heartbeat failed")

    verified = exact_claim()
    if not verified:
        raise RuntimeError("deploylane claim verification failed")

    print(json.dumps({
        "event": "ZCLOUD_DEPLOYLANE_CLAIMED",
        "project_id": PROJECT,
        "claim_key": CLAIM,
        "owner_id": owner(),
        "worker_id": WORKER,
        "main_sha": main_sha,
        "preflight_ok": True,
        "heartbeat_ok": True,
        "claim_verified": True,
    }, sort_keys=True))


def verify() -> None:
    claim = exact_claim()
    if not claim:
        raise RuntimeError("deploylane claim no longer matches owner_id/worker_id")
    print(json.dumps({
        "event": "ZCLOUD_DEPLOYLANE_CLAIM_VERIFIED",
        "project_id": PROJECT,
        "claim_key": CLAIM,
        "owner_id": owner(),
        "worker_id": WORKER,
        "lease_until": claim.get("lease_until"),
    }, sort_keys=True))


def release() -> None:
    result = post_json("/api/task-claims", {
        "action": "release",
        "project_id": PROJECT,
        "claim_key": CLAIM,
        "owner_id": owner(),
    })
    print(json.dumps({
        "event": "ZCLOUD_DEPLOYLANE_CLAIM_RELEASED",
        "project_id": PROJECT,
        "claim_key": CLAIM,
        "owner_id": owner(),
        "released": bool(result.get("released")),
    }, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["acquire", "verify", "release"])
    args = parser.parse_args()
    {"acquire": acquire, "verify": verify, "release": release}[args.action]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
