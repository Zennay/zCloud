#!/usr/bin/env python3
"""Fail-closed migration of the 14 repository-scoped GitHub Actions runners.

Run this ONLY on the new VPS with approved root access and gh authenticated
with Actions runner administration permissions. The source VPS must be drained
and its runner services disabled first. This script never copies registrations,
private keys or .credentials from the source.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

EXPECTED_HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
OWNER = "Zennay"
HOME = Path("/home/ubuntu")
DEST = HOME / "actions-runner-migrated"
# Repo, original registration name, existing binaries on the destination.
RUNNERS = (
    ("Discordbot", "vps-bb300bba", "/home/ubuntu/actions-runner-elmo"),
    ("Ftmo", "vps-bb300bba-ftmo", "/opt/ftmo-runner"),
    ("Haxlab", "vps-bb300bba-haxlab", "/opt/actions-runner-haxlab"),
    ("Lightup", "vps-bb300bba-lightup", "/home/ubuntu/actions-runner-lightup-proof"),
    ("RaiseAI", "vps-bb300bba", "/home/ubuntu/actions-runner-raiseai"),
    ("Supa", "vps-bb300bba-supa", "/home/ubuntu/actions-runner-supa"),
    ("Woonpoints", "vps-bb300bba-pool-Woonpoints", "/home/ubuntu/actions-runner-pool-Woonpoints"),
    ("zCloud", "zcloud-vps-1", "/home/ubuntu/actions-runner-zcloud"),
    ("zSSH", "zssh-vps-1", "/home/ubuntu/actions-runner-zssh"),
    ("fuckball", "vps-bb300bba-pool-fuckball", "/home/ubuntu/actions-runner-pool-fuckball"),
    ("ikea-enhanced-app", "vps-bb300bba-pool-ikea-enhanced-app", "/home/ubuntu/actions-runner-pool-ikea-enhanced-app"),
    ("Mixlab", "vps-bb300bba-pool-Mixlab", "/home/ubuntu/actions-runner-pool-Mixlab"),
    ("Tuddy", "vps-bb300bba-pool-Tuddy", "/home/ubuntu/actions-runner-pool-Tuddy"),
    ("zGuard", "vps-bb300bba-pool-zGuard", "/home/ubuntu/actions-runner-pool-zGuard"),
)
assert len(RUNNERS) == 14 and len({r[0] for r in RUNNERS}) == 14

def call(args: list[str], *, cwd: Path | None = None, user: str | None = None) -> str:
    command = ["runuser", "-u", user, "--", *args] if user else args
    # config.sh receives a short-lived registration token in argv. Treat any
    # config.sh/--token invocation as sensitive even when runuser wraps argv[0].
    sensitive = "--token" in command or any(Path(part).name == "config.sh" for part in command)
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=150)
    except subprocess.TimeoutExpired:
        # TimeoutExpired embeds the full command (including the token) in its
        # string form. Never let the outer HOLD handler print that object.
        raise RuntimeError("Command timed out; arguments and output redacted") from None
    if result.returncode:
        detail = "[redacted]" if sensitive else result.stderr[-250:]
        raise RuntimeError(
            f"Command failed in {cwd or 'cwd'}: exit {result.returncode}; stderr tail: {detail}"
        )
    return result.stdout

def api(path: str, *, post: bool = False) -> dict:
    args = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
    if post:
        args.extend(["-X", "POST"])
    result = subprocess.run([*args, path], text=True, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError(f"GitHub API unavailable for {path} (code {result.returncode})")
    return json.loads(result.stdout)

def get_runners(repo: str) -> list[dict]:
    data = api(f"repos/{OWNER}/{repo}/actions/runners?per_page=100")
    if data.get("total_count", 0) > 100:
        raise RuntimeError(f"{repo}: runner list pagination required; refusing")
    return data["runners"]

def new_name(repo: str) -> str:
    return "vm-7eb5ee16-" + re.sub(r"[^a-z0-9-]", "-", repo.lower())

def snapshot(repo: str, old_name: str) -> tuple[dict | None, dict | None]:
    runners = get_runners(repo)
    old = next((r for r in runners if r["name"] == old_name), None)
    new = next((r for r in runners if r["name"] == new_name(repo)), None)
    return old, new

def inventory(selected: list[tuple[str, str, str]]) -> None:
    print("repo | source registration | source state | new state | source custom labels")
    for repo, old_name, _ in selected:
        try:
            old, new = snapshot(repo, old_name)
            labels = ",".join(l["name"] for l in old.get("labels", [])
                              if l.get("type") == "custom") if old else "unknown"
            print(f"{repo} | {old_name} | "
                  f"{old['status']}/{old['busy'] if 'busy' in old else '?'}"
                  if old else f"{repo} | {old_name} | MISSING", end="")
            print(f" | {new['status'] if new else 'not registered'} | {labels}")
        except Exception as exc:
            print(f"{repo} | ERROR {type(exc).__name__}: {exc}")

def require_target() -> None:
    if os.uname().nodename != EXPECTED_HOST:
        raise RuntimeError(f"Wrong host: require {EXPECTED_HOST}")
    if os.geteuid() != 0:
        raise RuntimeError("Root needed to provision ubuntu systemd runner services")
    if not HOME.is_dir():
        raise RuntimeError("ubuntu home not restored")
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI missing on destination")
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise RuntimeError("GH_TOKEN/GITHUB_TOKEN must be provided securely on destination")
    call(["id", "ubuntu"])

def prepared_dir(repo: str, source: str) -> Path:
    source_path = Path(source)
    target = DEST / repo.lower()
    if not (source_path / "config.sh").is_file():
        raise RuntimeError(f"{repo}: runner binaries not restored to {source_path}")
    if source_path.resolve() == target.resolve():
        raise RuntimeError("Source and target runner path must differ")
    target.mkdir(parents=True, exist_ok=True)
    # Deliberately do not copy identity, credentials, workspaces, logs or sessions.
    call(["rsync", "-a", "--exclude=.runner", "--exclude=.runner_migrated",
          "--exclude=.credentials", "--exclude=.credentials_rsaparams",
          "--exclude=_work/", "--exclude=_diag/", "--exclude=.service",
          str(source_path) + "/", str(target) + "/"])
    call(["chown", "-R", "ubuntu:ubuntu", str(target)])
    if (target / ".runner").exists() or (target / ".credentials").exists():
        raise RuntimeError(f"{repo}: target unexpectedly contains old registration")
    return target

def migrate_one(repo: str, old_name: str, binary_dir: str) -> None:
    old, new = snapshot(repo, old_name)
    if new:
        raise RuntimeError(f"{repo}: new runner already registered; inspect before retrying")
    if not old:
        raise RuntimeError(f"{repo}: old runner missing; cannot copy its labels safely")
    if old.get("status") != "offline" or old.get("busy"):
        raise RuntimeError(f"{repo}: OLD RUNNER NOT DRAINED (status={old.get('status')}, busy={old.get('busy')})")
    labels = [l["name"] for l in old.get("labels", []) if l.get("type") == "custom"]
    if not labels:
        raise RuntimeError(f"{repo}: missing custom labels; do not register default-only runner")
    target = prepared_dir(repo, binary_dir)
    if (target / "svc.sh").exists() is False:
        raise RuntimeError(f"{repo}: missing svc.sh")
    token = api(f"repos/{OWNER}/{repo}/actions/runners/registration-token", post=True)["token"]
    name = new_name(repo)
    # --replace is forbidden; existing registrations must never be stolen.
    args = ["./config.sh", "--unattended", "--url", f"https://github.com/{OWNER}/{repo}",
            "--token", token, "--name", name, "--labels", ",".join(labels),
            "--work", "_work"]
    try:
        call(args, cwd=target, user="ubuntu")
    finally:
        token = None
        args = []
    call(["./svc.sh", "install", "ubuntu"], cwd=target)
    call(["./svc.sh", "start"], cwd=target)
    for _ in range(12):
        _, check = snapshot(repo, old_name)
        if check and check["status"] == "online":
            actual = {l["name"] for l in check.get("labels", [])}
            if not set(labels).issubset(actual):
                raise RuntimeError(f"{repo}: new runner online with incorrect labels")
            print(f"{repo}: online as {name}; custom labels={','.join(labels)}")
            return
        time.sleep(5)
    raise RuntimeError(f"{repo}: registered but not yet online; investigate service")

def verify_jobs(selected: list[tuple[str, str, str]]) -> None:
    """Actual assignment receipt, not just runner-online status."""
    for repo, _old, _source in selected:
        desired = new_name(repo)
        try:
            runs = api(f"repos/{OWNER}/{repo}/actions/runs?per_page=30")["workflow_runs"]
            receipts = []
            for run in runs[:12]:
                jobs = api(f"repos/{OWNER}/{repo}/actions/runs/{run['id']}/jobs?per_page=100")
                if jobs.get("total_count", 0) > 100:
                    continue
                for job in jobs.get("jobs", []):
                    if job.get("runner_name") == desired and job.get("runner_id", 0) > 0:
                        receipts.append((run["id"], job["id"], job["status"], job.get("conclusion")))
            print(f"{repo}: assigned jobs={receipts[:3] if receipts else 'NONE YET — not proven'}")
        except Exception as exc:
            print(f"{repo}: job proof unavailable: {type(exc).__name__}")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("inventory", "migrate", "verify-jobs"), default="inventory")
    parser.add_argument("--repo", help="Select one of the 14 repositories; default is all")
    parser.add_argument("--old-services-disabled", action="store_true",
                        help="Operator confirms source systemd units are disabled and no Runner.Worker jobs remain")
    args = parser.parse_args()
    selected = [r for r in RUNNERS if not args.repo or r[0].lower() == args.repo.lower()]
    if not selected:
        parser.error("Unknown repository")
    if args.action == "inventory":
        inventory(selected)
    elif args.action == "verify-jobs":
        verify_jobs(selected)
    else:
        require_target()
        if not args.old_services_disabled:
            raise RuntimeError("Must explicitly confirm old services drained and disabled")
        failures = 0
        for repo, old_name, binaries in selected:
            try:
                migrate_one(repo, old_name, binaries)
            except Exception as exc:
                failures += 1
                print(f"{repo}: HOLD — {type(exc).__name__}: {exc}", file=sys.stderr)
        if failures:
            print(f"Migration incomplete: {len(selected) - failures}/{len(selected)} newly online", file=sys.stderr)
            return 2
        print(f"Registered and online: {len(selected)}. Run --action verify-jobs after safe job dispatch.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"HOLD: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
