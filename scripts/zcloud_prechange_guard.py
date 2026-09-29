#!/usr/bin/env python3
"""Fail-closed pre-change guard for zCloud deployments.

The live checkout is intentionally not normalized to canonical Git history, so this
guard compares the current managed source tree with the recorded last-known-good
snapshot instead of assuming a clean git checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DEFAULT_STATE = Path(os.environ.get(
    "ZCLOUD_RECOVERY_DIR",
    str(Path.home() / ".local/state/zcloud/recovery"),
))
DEFAULT_RUNTIME_EXTENSION = Path(os.environ.get(
    "ZCLOUD_FIREFOX_RUNTIME_EXTENSION",
    str(Path.home() / "snap/firefox/common/chatgpt-project-extension/background.js"),
))
DEFAULT_HEALTH_URL = os.environ.get(
    "ZCLOUD_HEALTH_URL", "http://127.0.0.1:8765/api/status"
)
MANAGED_PATHS = (
    "server.py",
    "enhancements.py",
    "projects.json",
    "project-layout.json",
    "resource-policy.json",
    "portfolio_queue.seed.json",
    "public",
    "firefox-extension",
    "deploy",
    "scripts",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in MANAGED_PATHS:
        base = root / rel
        if not base.exists():
            continue
        files = [base] if base.is_file() else sorted(
            p for p in base.rglob("*") if p.is_file() and not p.is_symlink()
        )
        for path in files:
            hashes[path.relative_to(root).as_posix()] = sha256_file(path)
    return hashes


def load_lkg(state: Path) -> tuple[Path, dict]:
    pointer = state / "last-known-good.json"
    if not pointer.exists():
        raise RuntimeError("no last-known-good pointer")
    data = json.loads(pointer.read_text())
    snapshot = state / "snapshots" / str(data["snapshot_id"])
    manifest = json.loads((snapshot / "manifest.json").read_text())
    expected = manifest.get("hashes") or {}
    actual: dict[str, str] = {}
    files_root = snapshot / "files"
    if files_root.exists():
        for path in sorted(p for p in files_root.rglob("*") if p.is_file() and not p.is_symlink()):
            actual[path.relative_to(files_root).as_posix()] = sha256_file(path)
    if actual != expected:
        raise RuntimeError("last-known-good snapshot hash verification failed")
    return snapshot, manifest


def changed_paths(current: dict[str, str], baseline: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(current) | set(baseline)
        if current.get(path) != baseline.get(path)
    )


def service_active(service: str, *, user: bool = False) -> bool:
    cmd = ["systemctl"]
    env = os.environ.copy()
    if user:
        cmd.append("--user")
        env.update({
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        })
    cmd.extend(["is-active", "--quiet", service])
    return subprocess.run(cmd, env=env).returncode == 0


def http_healthy(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def evaluate(
    root: Path,
    state: Path,
    runtime_extension: Path | None,
    allowed_changes: set[str] | None = None,
    live_checks: bool = True,
    health_url: str = DEFAULT_HEALTH_URL,
) -> dict:
    allowed = allowed_changes or set()
    checks: list[dict] = []
    try:
        _, manifest = load_lkg(state)
    except Exception as exc:
        return {
            "ok": False,
            "checks": [{"name": "lkg_integrity", "ok": False, "detail": str(exc)}],
            "unexpected_changes": [],
        }

    checks.append({
        "name": "lkg_integrity",
        "ok": True,
        "detail": manifest.get("snapshot_id"),
    })
    current = tree_hashes(root)
    baseline = manifest.get("hashes") or {}
    changed = changed_paths(current, baseline)
    unexpected = [path for path in changed if path not in allowed]
    checks.append({
        "name": "managed_source_state",
        "ok": not unexpected,
        "detail": {
            "changed": changed,
            "allowed": sorted(allowed),
            "unexpected": unexpected,
        },
    })

    source_extension = root / "firefox-extension/background.js"
    if runtime_extension and source_extension.exists() and runtime_extension.exists():
        source_hash = sha256_file(source_extension)
        runtime_hash = sha256_file(runtime_extension)
        checks.append({
            "name": "firefox_source_runtime_match",
            "ok": source_hash == runtime_hash,
            "detail": {"source": source_hash, "runtime": runtime_hash},
        })
    else:
        checks.append({
            "name": "firefox_source_runtime_match",
            "ok": False,
            "detail": "source or runtime Firefox extension missing",
        })

    if live_checks:
        checks.extend([
            {
                "name": "zcloud_service",
                "ok": service_active("zennay-cloud.service"),
                "detail": "system service active",
            },
            {
                "name": "firefox_service",
                "ok": service_active("chatgpt-firefox.service", user=True),
                "detail": "user service active",
            },
            {
                "name": "zcloud_http",
                "ok": http_healthy(health_url),
                "detail": health_url,
            },
        ])

    return {
        "ok": all(item["ok"] for item in checks),
        "snapshot_id": manifest.get("snapshot_id"),
        "checks": checks,
        "unexpected_changes": unexpected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate zCloud before a production change")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--runtime-extension", type=Path, default=DEFAULT_RUNTIME_EXTENSION)
    parser.add_argument("--allow-change", action="append", default=[])
    parser.add_argument("--skip-live-checks", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = evaluate(
        args.root.resolve(),
        args.state.resolve(),
        args.runtime_extension.resolve(),
        set(args.allow_change),
        live_checks=not args.skip_live_checks,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            mark = "OK" if check["ok"] else "FAIL"
            print(f"{mark:4} {check['name']}: {check['detail']}")
        print("PRECHANGE_GREEN" if result["ok"] else "PRECHANGE_BLOCKED")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
