#!/usr/bin/env python3
"""Bounded GitHub issue-comment to VPS runner operations.

This is NOT a generic shell interpreter.  Extend reviewed recipes on main for
new operations.  Public issue comments and Actions logs must contain no secrets.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile

REPOSITORY = "Zennay/zCloud"
CONTROL_ISSUE = 1278
OWNER = "Zennay"
CHATGPT_APP_ID = 1144995
HOST = "vps-bb300bba"
USER = "ubuntu"
COMMAND = re.compile(
    r"/zssh (status|project cloud|service (?:zennay-cloud|zssh|zssh-public)|run (?:write-probe|zcloud-guard-test))\Z"
)


class RequestRejected(ValueError):
    pass


def parse_request(event: dict, actor: str) -> str:
    if event.get("action") != "created":
        raise RequestRejected("wrong_action")
    if (event.get("repository") or {}).get("full_name") != REPOSITORY:
        raise RequestRejected("wrong_repository")
    issue = event.get("issue") or {}
    if issue.get("number") != CONTROL_ISSUE or issue.get("pull_request"):
        raise RequestRejected("wrong_issue")
    comment = event.get("comment") or {}
    if actor != OWNER or (comment.get("user") or {}).get("login") != OWNER:
        raise RequestRejected("unauthorized_actor")
    if comment.get("author_association") != "OWNER":
        raise RequestRejected("unauthorized_author_association")
    # Manual owner comments are allowed. For connector-mediated requests,
    # require the exact observed GitHub App identity; never trust text claims.
    app = comment.get("performed_via_github_app")
    if app is not None and app.get("id") != CHATGPT_APP_ID:
        raise RequestRejected("untrusted_app")
    body = comment.get("body")
    if not isinstance(body, str) or len(body) > 120 or not COMMAND.fullmatch(body):
        raise RequestRejected("unsupported_operation")
    return body.removeprefix("/zssh ")


def ensure_runner() -> None:
    if socket.gethostname().split(".", 1)[0] != HOST:
        raise RuntimeError("wrong_runner_host")
    if os.environ.get("USER") != USER or os.geteuid() == 0:
        raise RuntimeError("wrong_runner_user")
    name = os.environ.get("RUNNER_NAME", "")
    if not name or "haxlab" in name.lower():
        raise RuntimeError("wrong_runner_identity")


def run_checked(argv: list[str], *, cwd: str | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=20,
        check=False,
        env={k: v for k, v in os.environ.items() if k not in
             {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY"}},
    )
    # Only reviewed read-only commands may expose stdout; bounded and no stderr.
    return completed.returncode, completed.stdout[:800].strip()


def execute(request: str) -> dict:
    ensure_runner()
    if request == "status":
        probes = [
            ("uptime", ["/usr/bin/uptime", "-p"]),
            ("disk", ["/usr/bin/df", "-h", "/"]),
            ("memory", ["/usr/bin/free", "-h"]),
        ]
        checks = {}
        for label, argv in probes:
            code, output = run_checked(argv)
            if code:
                raise RuntimeError("status_probe_failed:" + label)
            checks[label] = output
        return {"ok": True, "operation": "status", "host": HOST, "checks": checks}
    if request == "project cloud":
        code, sha = run_checked(
            ["/usr/bin/git", "-C", "/home/ubuntu/zennay-cloud", "rev-parse", "--short", "HEAD"]
        )
        if code or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise RuntimeError("cloud_git_revision_unavailable")
        return {"ok": True, "operation": "project cloud", "revision": sha}
    if request.startswith("service "):
        service = request.split(" ", 1)[1] + ".service"
        code, active = run_checked(["/usr/bin/systemctl", "is-active", "--", service])
        if code not in (0, 3):
            raise RuntimeError("service_inspection_failed")
        if active not in ("active", "inactive", "failed", "activating", "deactivating", "unknown"):
            raise RuntimeError("unexpected_service_state")
        return {"ok": True, "operation": request, "state": active}
    if request == "run write-probe":
        # Material, reversible proof of writing on the VPS without modifying services.
        temp_root = os.environ.get("RUNNER_TEMP")
        if not temp_root or not Path(temp_root).is_dir():
            raise RuntimeError("runner_temp_unavailable")
        with tempfile.TemporaryDirectory(prefix="zssh-chatgpt-", dir=temp_root) as directory:
            probe = Path(directory) / "proof.txt"
            probe.write_text("runner-local-write-proof\n", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "runner-local-write-proof\n":
                raise RuntimeError("write_probe_mismatch")
        return {"ok": True, "operation": request, "effect": "temporary_file_write_verified_and_cleaned"}
    if request == "run zcloud-guard-test":
        code, _ = run_checked(
            [sys.executable, "-m", "unittest", "-q", "tests.test_vps_runner_guard"],
            cwd=os.environ.get("GITHUB_WORKSPACE") or None,
        )
        if code:
            raise RuntimeError("zcloud_guard_test_failed")
        return {"ok": True, "operation": request, "effect": "unit_test_passed"}
    raise RequestRejected("unsupported_operation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["validate", "execute"])
    parser.add_argument("--event", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "")))
    args = parser.parse_args()
    if not args.event.is_file():
        print("zssh_bridge_rejected: missing_event", file=sys.stderr)
        return 2
    try:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        request = parse_request(event, os.environ.get("GITHUB_ACTOR", ""))
        if args.mode == "validate":
            print("ZSSH_BRIDGE_AUTHORIZED " + request)
            return 0
        result = execute(request)
        # The only operational output is a JSON receipt: no environment, secrets,
        # stderr, arbitrary shell stdout, private files, or dynamic paths.
        print("ZSSH_BRIDGE_RECEIPT=" + json.dumps(result, sort_keys=True))
        return 0
    except (RequestRejected, RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Error messages are machine-owned constants, not raw command stderr.
        print("zssh_bridge_rejected: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
