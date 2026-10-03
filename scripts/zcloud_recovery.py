#!/usr/bin/env python3
"""Transactional last-known-good snapshots and rollback for the zCloud control-plane.

The recovery layer intentionally never snapshots or restores runtime state such as
history.db, tokens, TLS keys, signals or repository metadata. Project/chat mappings
therefore survive source/config rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FORMAT_VERSION = 1
DEFAULT_ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DEFAULT_STATE = Path(os.environ.get("ZCLOUD_RECOVERY_DIR", str(Path.home() / ".local/state/zcloud/recovery")))
SERVICE = os.environ.get("ZCLOUD_SERVICE", "zennay-cloud.service")
BROWSER_SERVICE = os.environ.get("ZCLOUD_BROWSER_SERVICE", "chatgpt-firefox.service")
HEALTH_URL = os.environ.get("ZCLOUD_HEALTH_URL", "http://127.0.0.1:8765/")
# Keep rollback verification on the same bounded budget as production startup.
# The live unit advertises TimeoutStartUSec=1min 30s.
SERVICE_HEALTH_TIMEOUT_SECONDS = float(
    os.environ.get("ZCLOUD_SERVICE_HEALTH_TIMEOUT_SECONDS", "90")
)
LKG_CAPTURE_HEALTH_TIMEOUT_SECONDS = float(
    os.environ.get("ZCLOUD_LKG_CAPTURE_HEALTH_TIMEOUT_SECONDS", "30")
)

MANAGED_PATHS = (
    "server.py",
    "project_runtime.py",
    "lane_generator.py",
    "enhancements.py",
    "projects.json",
    "project-layout.json",
    "resource-policy.json",
    "project-contracts.json",
    "portfolio_queue.seed.json",
    "public",
    "firefox-extension",
    "deploy",
    "scripts",
)
PERSISTENT_PATHS = (
    "history.db",
    "history.db-wal",
    "history.db-shm",
    ".watch-token",
    ".action-allowed-ips",
    "watch-tls.crt",
    "watch-tls.key",
    "signals",
    "alert-state.json",
    ".git",
    "repos",
)


class RecoveryError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(args, *, check=False, env=None):
    p = subprocess.run(args, text=True, capture_output=True, env=env)
    if check and p.returncode:
        raise RecoveryError(f"command failed ({p.returncode}): {' '.join(args)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(base: Path) -> dict[str, str]:
    hashes = {}
    if not base.exists():
        return hashes
    for path in sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink()):
        hashes[path.relative_to(base).as_posix()] = sha256_file(path)
    return hashes


def copy_path(src: Path, dst: Path) -> None:
    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dst, symlinks=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dst.symlink_to(os.readlink(src))
        else:
            shutil.copy2(src, dst)


def snapshot_managed(root: Path, dest: Path) -> list[str]:
    present = []
    dest.mkdir(parents=True, exist_ok=True)
    for rel in MANAGED_PATHS:
        src = root / rel
        if src.exists() or src.is_symlink():
            copy_path(src, dest / rel)
            present.append(rel)
    return present


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def restore_managed(source: Path, root: Path, *, fail_after: int | None = None) -> None:
    completed = 0
    for rel in MANAGED_PATHS:
        src = source / rel
        dst = root / rel
        staged = root / f".zcloud-restore-{os.getpid()}-{rel.replace('/', '_')}"
        remove_path(staged)
        if src.exists() or src.is_symlink():
            copy_path(src, staged)
            old = root / f".zcloud-old-{os.getpid()}-{rel.replace('/', '_')}"
            remove_path(old)
            if dst.exists() or dst.is_symlink():
                dst.rename(old)
            staged.rename(dst)
            remove_path(old)
        else:
            remove_path(dst)
        completed += 1
        if fail_after is not None and completed >= fail_after:
            raise RecoveryError("simulated restore failure")


def git_meta(root: Path) -> dict:
    def value(*args):
        p = run(["git", "-C", str(root), *args])
        return p.stdout.strip() if p.returncode == 0 else None

    return {
        "branch": value("branch", "--show-current"),
        "head": value("rev-parse", "HEAD"),
        "origin_main": value("rev-parse", "origin/main"),
        "merge_base": value("merge-base", "HEAD", "origin/main"),
        "dirty": (value("status", "--short") or "").splitlines(),
    }


def service_meta(service: str = SERVICE) -> dict:
    p = run([
        "systemctl", "show", service,
        "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
        "-p", "ExecStart", "-p", "WorkingDirectory", "-p", "FragmentPath",
    ])
    if p.returncode:
        return {"service": service, "active_state": "unknown", "error": (p.stderr or p.stdout).strip()[:200]}
    raw = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    return {
        "service": service,
        "active_state": raw.get("ActiveState"),
        "sub_state": raw.get("SubState"),
        "main_pid": int(raw.get("MainPID") or 0),
        "exec_start": raw.get("ExecStart") or None,
        "working_directory": raw.get("WorkingDirectory") or None,
        "fragment_path": raw.get("FragmentPath") or None,
    }


def browser_service_meta() -> dict:
    env = os.environ.copy()
    env.update({"XDG_RUNTIME_DIR": "/run/user/1000", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"})
    p = run(["systemctl", "--user", "show", BROWSER_SERVICE, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "FragmentPath"], env=env)
    if p.returncode:
        return {"service": BROWSER_SERVICE, "active_state": "unknown"}
    raw = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    return {"service": BROWSER_SERVICE, "active_state": raw.get("ActiveState"), "sub_state": raw.get("SubState"), "main_pid": int(raw.get("MainPID") or 0), "fragment_path": raw.get("FragmentPath") or None}


def http_healthy(url: str = HEALTH_URL, timeout=8) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def wait_http_healthy(
    timeout: float = LKG_CAPTURE_HEALTH_TIMEOUT_SECONDS,
    interval: float = 0.5,
) -> bool:
    """Bound transient HTTP gaps before declaring an LKG capture unhealthy."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if http_healthy():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(max(0.01, float(interval)), remaining))


def mapping_fingerprint(db: Path) -> dict:
    if not db.exists():
        return {"available": False}
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        c.row_factory = sqlite3.Row
        targets = [dict(r) for r in c.execute("SELECT project_id,conversation_id FROM runner_targets ORDER BY project_id")]
        workers = [dict(r) for r in c.execute("SELECT project_id,worker_slot,conversation_id FROM runner_workers ORDER BY project_id,worker_slot")]
        c.close()
        payload = json.dumps({"targets": targets, "workers": workers}, sort_keys=True, separators=(",", ":")).encode()
        return {"available": True, "sha256": hashlib.sha256(payload).hexdigest(), "targets": len(targets), "workers": len(workers)}
    except Exception as exc:
        return {"available": False, "error": str(exc)[:200]}


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def append_log(state: Path, event: str, **fields) -> None:
    state.mkdir(parents=True, exist_ok=True)
    row = {"time": utc_now(), "event": event, **fields}
    with (state / "recovery.log").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_lkg(state: Path) -> tuple[Path, dict]:
    pointer = state / "last-known-good.json"
    if not pointer.exists():
        raise RecoveryError("no last-known-good snapshot recorded")
    ptr = json.loads(pointer.read_text())
    snap = state / "snapshots" / ptr["snapshot_id"]
    manifest = json.loads((snap / "manifest.json").read_text())
    if manifest.get("format_version") != FORMAT_VERSION:
        raise RecoveryError("unsupported recovery snapshot format")
    expected = manifest.get("hashes") or {}
    actual = tree_hashes(snap / "files")
    if expected != actual:
        raise RecoveryError("last-known-good snapshot hash verification failed")
    return snap, manifest


def capture(root: Path, state: Path, evidence: str, *, require_health=True, service_state: dict | None = None, browser_state: dict | None = None) -> dict:
    root = root.resolve()
    if not evidence.strip():
        raise RecoveryError("evidence is required before marking last-known-good")
    svc = service_state or service_meta()
    browser = browser_state or browser_service_meta()
    if require_health and (
        svc.get("active_state") != "active" or not wait_http_healthy()
    ):
        raise RecoveryError("zCloud is not healthy; refusing to mark last-known-good")
    git = git_meta(root)
    short = (git.get("head") or "working-tree")[:8]
    snap_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + short
    final = state / "snapshots" / snap_id
    tmp = state / "snapshots" / ("." + snap_id + ".tmp")
    remove_path(tmp)
    files = tmp / "files"
    present = snapshot_managed(root, files)
    manifest = {
        "format_version": FORMAT_VERSION,
        "snapshot_id": snap_id,
        "created_at": utc_now(),
        "root": str(root),
        "evidence": evidence.strip(),
        "managed_paths": list(MANAGED_PATHS),
        "persistent_paths_never_restored": list(PERSISTENT_PATHS),
        "present_paths": present,
        "hashes": tree_hashes(files),
        "git": git,
        "service": svc,
        "browser_service": browser,
        "mapping_fingerprint": mapping_fingerprint(root / "history.db"),
        "config_hashes": {rel: sha256_file(root / rel) for rel in ("projects.json", "project-layout.json", "resource-policy.json", "project-contracts.json") if (root / rel).is_file()},
    }
    write_json_atomic(tmp / "manifest.json", manifest)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, final)
    write_json_atomic(state / "last-known-good.json", {"snapshot_id": snap_id, "manifest": str(final / "manifest.json"), "updated_at": utc_now()})
    append_log(state, "lkg_captured", snapshot_id=snap_id, git_head=git.get("head"), evidence=evidence.strip())
    return manifest


def systemctl_command(action: str) -> list[str]:
    if os.geteuid() == 0:
        return ["systemctl", action, SERVICE]
    return ["sudo", "-n", "systemctl", action, SERVICE]


def set_service(active: bool) -> None:
    action = "start" if active else "stop"
    run(systemctl_command(action), check=True)


def wait_service_healthy(active: bool, timeout=SERVICE_HEALTH_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = service_meta().get("active_state") == "active"
        if current == active and (not active or http_healthy()):
            return
        time.sleep(0.5)
    raise RecoveryError("service health did not reach expected state after rollback")


def rollback(root: Path, state: Path, *, manage_service=True, verify_health=True, test_fail_after: int | None = None) -> dict:
    root = root.resolve()
    snap, manifest = load_lkg(state)
    before_mapping = mapping_fingerprint(root / "history.db")
    before_service = service_meta() if manage_service else {"active_state": "inactive"}
    target_active = manifest.get("service", {}).get("active_state") == "active"
    tx_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    tx = state / "transactions" / tx_id
    pre = tx / "pre-rollback-files"
    snapshot_managed(root, pre)
    append_log(state, "rollback_started", transaction_id=tx_id, snapshot_id=manifest["snapshot_id"], mapping=before_mapping)
    try:
        if manage_service and before_service.get("active_state") == "active":
            set_service(False)
        restore_managed(snap / "files", root, fail_after=test_fail_after)
        if manage_service:
            set_service(target_active)
        if verify_health and manage_service:
            wait_service_healthy(target_active)
        after_mapping = mapping_fingerprint(root / "history.db")
        if before_mapping.get("available") and after_mapping.get("sha256") != before_mapping.get("sha256"):
            raise RecoveryError("project/chat mapping changed during rollback")
        append_log(state, "rollback_succeeded", transaction_id=tx_id, snapshot_id=manifest["snapshot_id"], mapping=after_mapping)
        return {"ok": True, "snapshot_id": manifest["snapshot_id"], "mapping": after_mapping, "service_target_active": target_active}
    except Exception as exc:
        try:
            if manage_service:
                run(systemctl_command("stop"))
            restore_managed(pre, root)
            if manage_service:
                set_service(before_service.get("active_state") == "active")
                if verify_health:
                    wait_service_healthy(before_service.get("active_state") == "active")
            reverted_mapping = mapping_fingerprint(root / "history.db")
            append_log(state, "rollback_failed_reverted", transaction_id=tx_id, snapshot_id=manifest["snapshot_id"], error=str(exc)[:300], mapping=reverted_mapping)
        except Exception as revert_exc:
            append_log(state, "rollback_failed_revert_failed", transaction_id=tx_id, snapshot_id=manifest["snapshot_id"], error=str(exc)[:300], revert_error=str(revert_exc)[:300])
            raise RecoveryError(f"rollback failed and automatic revert also failed: {exc}; revert: {revert_exc}") from revert_exc
        raise RecoveryError(f"rollback failed; pre-rollback files were restored: {exc}") from exc


def status(root: Path, state: Path) -> dict:
    result = {"recovery_dir": str(state), "current": {"git": git_meta(root), "service": service_meta(), "browser_service": browser_service_meta(), "mapping": mapping_fingerprint(root / "history.db")}}
    try:
        _, manifest = load_lkg(state)
        result["last_known_good"] = {k: manifest.get(k) for k in ("snapshot_id", "created_at", "evidence", "git", "service", "browser_service", "mapping_fingerprint", "config_hashes")}
    except Exception as exc:
        result["last_known_good"] = None
        result["lkg_error"] = str(exc)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="zCloud last-known-good recovery guard")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("capture", help="mark current healthy tree as last-known-good")
    c.add_argument("--evidence", required=True, help="green smoke/canary evidence for this state")
    sub.add_parser("status", help="show current and last-known-good state")
    sub.add_parser("rollback", help="restore the last-known-good source/config snapshot")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            out = capture(args.root, args.state_dir, args.evidence)
        elif args.command == "rollback":
            out = rollback(args.root, args.state_dir)
        else:
            out = status(args.root, args.state_dir)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except RecoveryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())