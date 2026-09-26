#!/usr/bin/env python3
"""Guarded transactional promotion for the zCloud live control plane.

This tool deliberately does not normalize the live git checkout. It promotes an
explicit allowlist of candidate files into the existing live tree, preserving
persistent runtime state. Every production run is gated by:
  1. pre-change guard
  2. pre-promotion LKG capture
  3. per-file atomic replacement with immediate partial-write recovery
  4. zCloud service restart
  5. optional Firefox runtime sync + in-place addon reload
  6. read-only post-deploy canary with mapping preservation
  7. new LKG capture only after the canary is green

Any failure after source promotion invokes the existing LKG rollback. Firefox
runtime bytes are separately restored because they live outside the recovery
snapshot.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DEFAULT_STATE = Path(os.environ.get(
    "ZCLOUD_RECOVERY_DIR",
    str(Path.home() / ".local/state/zcloud/recovery"),
))
DEFAULT_PRECHANGE = Path(os.environ.get(
    "ZCLOUD_PRECHANGE_GUARD",
    str(Path.home() / ".local/bin/zcloud-prechange-guard"),
))
DEFAULT_POSTDEPLOY = Path(os.environ.get(
    "ZCLOUD_POSTDEPLOY_CANARY",
    str(Path.home() / ".local/bin/zcloud-postdeploy-canary"),
))
DEFAULT_RUNTIME_EXTENSION = Path(os.environ.get(
    "ZCLOUD_FIREFOX_RUNTIME_EXTENSION",
    str(Path.home() / "snap/firefox/common/chatgpt-project-extension/background.js"),
))
DEFAULT_RELOAD_HELPER = Path(os.environ.get(
    "ZCLOUD_RELOAD_HELPER",
    str(Path.home() / ".local/bin/zcloud-reload-extension.mjs"),
))
SERVICE = os.environ.get("ZCLOUD_SERVICE", "zennay-cloud.service")
HEALTH_URL = os.environ.get("ZCLOUD_HEALTH_URL", "http://127.0.0.1:8765/api/status")

DEFAULT_PATHS = (
    "server.py",
    "enhancements.py",
    "firefox-extension/background.js",
    "public/app.js",
    "public/enhancements.js",
    "public/enhancements.css",
)

BLOCKED_PREFIXES = (
    ".git",
    "history.db",
    ".watch-token",
    ".action-allowed-ips",
    "watch-tls.",
    "signals",
    "repos",
    "alert-state.json",
)


class PromotionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, text=True, capture_output=True)
    if check and proc.returncode:
        raise PromotionError(
            f"command failed ({proc.returncode}): {' '.join(args)}: "
            f"{(proc.stderr or proc.stdout).strip()[:500]}"
        )
    return proc


def parse_json_output(proc: subprocess.CompletedProcess, label: str) -> dict:
    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:
        raise PromotionError(f"{label} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise PromotionError(f"{label} returned non-object JSON")
    return payload


def validate_relpath(rel: str) -> str:
    raw = str(rel or "").strip().replace("\\", "/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise PromotionError(f"unsafe promotion path: {rel!r}")
    normalized = path.as_posix()
    if normalized in ("", "."):
        raise PromotionError(f"unsafe promotion path: {rel!r}")
    if any(
        normalized == prefix
        or normalized.startswith(prefix + "/")
        or (prefix == "history.db" and normalized.startswith("history.db-"))
        or (prefix.endswith(".") and normalized.startswith(prefix))
        for prefix in BLOCKED_PREFIXES
    ):
        raise PromotionError(f"persistent/runtime path cannot be promoted: {normalized}")
    return normalized


def validate_candidate(candidate: Path, root: Path, paths: list[str]) -> dict[str, str]:
    candidate = candidate.resolve()
    root = root.resolve()
    if candidate == root:
        raise PromotionError("candidate directory must be separate from live root")
    hashes: dict[str, str] = {}
    for raw in paths:
        rel = validate_relpath(raw)
        source = candidate / rel
        target = root / rel
        if not source.is_file():
            raise PromotionError(f"candidate file missing: {rel}")
        if not target.exists() or not target.is_file():
            raise PromotionError(f"live target file missing: {rel}")
        hashes[rel] = sha256_file(source)
    return hashes


def syntax_check(candidate: Path, paths: list[str]) -> None:
    python_files = [str(candidate / rel) for rel in paths if rel.endswith(".py")]
    js_files = [str(candidate / rel) for rel in paths if rel.endswith((".js", ".mjs"))]
    if python_files:
        run([sys.executable, "-m", "py_compile", *python_files])
    for js in js_files:
        run(["node", "--check", js])


def http_healthy(url: str = HEALTH_URL, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def wait_http(timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if http_healthy():
            return
        time.sleep(0.5)
    raise PromotionError("zCloud HTTP health did not recover after restart")


def service_restart() -> None:
    cmd = ["systemctl", "restart", SERVICE] if os.geteuid() == 0 else [
        "sudo", "-n", "systemctl", "restart", SERVICE
    ]
    run(cmd)
    wait_http()


def mapping_from_recovery_status(root: Path, state: Path) -> str:
    recovery = root / "scripts/zcloud_recovery.py"
    proc = run([
        sys.executable, str(recovery),
        "--root", str(root),
        "--state-dir", str(state),
        "status",
    ])
    payload = parse_json_output(proc, "recovery status")
    mapping = ((payload.get("current") or {}).get("mapping") or {})
    value = mapping.get("sha256")
    if not value:
        raise PromotionError("current project/chat mapping fingerprint unavailable")
    return str(value)


def run_prechange(prechange: Path, root: Path, state: Path) -> dict:
    proc = run([
        str(prechange),
        "--root", str(root),
        "--state", str(state),
        "--json",
    ], check=False)
    payload = parse_json_output(proc, "pre-change guard")
    if proc.returncode or not payload.get("ok"):
        raise PromotionError("pre-change guard is not green")
    return payload


def capture_lkg(root: Path, state: Path, evidence: str) -> dict:
    proc = run([
        sys.executable,
        str(root / "scripts/zcloud_recovery.py"),
        "--root", str(root),
        "--state-dir", str(state),
        "capture",
        "--evidence", evidence,
    ])
    return parse_json_output(proc, "LKG capture")


def rollback_lkg(root: Path, state: Path) -> dict:
    proc = run([
        sys.executable,
        str(root / "scripts/zcloud_recovery.py"),
        "--root", str(root),
        "--state-dir", str(state),
        "rollback",
    ])
    return parse_json_output(proc, "LKG rollback")


def run_postdeploy(
    postdeploy: Path,
    *,
    root: Path,
    expected_mapping_sha: str,
    require_worker_read_model: bool,
    require_incidents: bool,
) -> dict:
    args = [
        str(postdeploy),
        "--root", str(root),
        "--db", str(root / "history.db"),
        "--expect-mapping-sha", expected_mapping_sha,
        "--json",
    ]
    if require_worker_read_model:
        args.append("--require-worker-read-model")
    if require_incidents:
        args.append("--require-incidents")
    proc = run(args, check=False)
    payload = parse_json_output(proc, "post-deploy canary")
    if proc.returncode or not payload.get("ok"):
        raise PromotionError("post-deploy canary is not green")
    return payload


def write_json_line(path: Path, event: str, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": utc_now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


@contextmanager
def promotion_lock(state: Path):
    lock_path = state / "promotion.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PromotionError("another zCloud promotion is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def transactional_replace(
    candidate: Path,
    root: Path,
    paths: list[str],
    tx_root: Path,
    *,
    fail_after: int | None = None,
) -> dict[str, str]:
    tx_root.mkdir(parents=True, exist_ok=False)
    staged = tx_root / "staged"
    backup = tx_root / "backup"
    staged.mkdir()
    backup.mkdir()
    normalized = [validate_relpath(rel) for rel in paths]

    for rel in normalized:
        src = candidate / rel
        stage = staged / rel
        stage.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, stage)

    completed: list[str] = []
    try:
        for rel in normalized:
            dst = root / rel
            old = backup / rel
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, old)
            os.replace(staged / rel, dst)
            completed.append(rel)
            if fail_after is not None and len(completed) >= fail_after:
                raise PromotionError("simulated promotion failure")
    except Exception:
        for rel in reversed(completed):
            old = backup / rel
            if old.exists():
                os.replace(old, root / rel)
        raise

    return {rel: sha256_file(root / rel) for rel in normalized}


def sync_firefox_runtime(
    root: Path,
    runtime_extension: Path,
    reload_helper: Path,
    tx_root: Path,
) -> Path | None:
    source = root / "firefox-extension/background.js"
    if not source.exists():
        return None
    backup = tx_root / "firefox-runtime.backup.js"
    if runtime_extension.exists():
        shutil.copy2(runtime_extension, backup)
    runtime_extension.parent.mkdir(parents=True, exist_ok=True)
    stage = runtime_extension.with_name(runtime_extension.name + f".zcloud-new-{os.getpid()}")
    shutil.copy2(source, stage)
    os.replace(stage, runtime_extension)
    try:
        if not reload_helper.exists():
            raise PromotionError(f"Firefox reload helper missing: {reload_helper}")
        run(["node", str(reload_helper)])
        if sha256_file(source) != sha256_file(runtime_extension):
            raise PromotionError("Firefox source/runtime mismatch after sync")
    except Exception:
        if backup.exists():
            restore_stage = runtime_extension.with_name(
                runtime_extension.name + f".zcloud-sync-revert-{os.getpid()}"
            )
            shutil.copy2(backup, restore_stage)
            os.replace(restore_stage, runtime_extension)
            if reload_helper.exists():
                try:
                    run(["node", str(reload_helper)])
                except Exception:
                    pass
        raise
    return backup if backup.exists() else None


def restore_firefox_runtime(
    runtime_extension: Path,
    runtime_backup: Path | None,
    reload_helper: Path,
) -> None:
    if runtime_backup and runtime_backup.exists():
        stage = runtime_extension.with_name(runtime_extension.name + f".zcloud-rollback-{os.getpid()}")
        shutil.copy2(runtime_backup, stage)
        os.replace(stage, runtime_extension)
        if reload_helper.exists():
            run(["node", str(reload_helper)])


def promote(
    candidate: Path,
    root: Path,
    state: Path,
    paths: list[str],
    *,
    prechange: Path = DEFAULT_PRECHANGE,
    postdeploy: Path = DEFAULT_POSTDEPLOY,
    runtime_extension: Path = DEFAULT_RUNTIME_EXTENSION,
    reload_helper: Path = DEFAULT_RELOAD_HELPER,
    require_worker_read_model: bool = False,
    require_incidents: bool = False,
    dry_run: bool = False,
) -> dict:
    candidate = candidate.resolve()
    root = root.resolve()
    state = state.resolve()
    normalized = [validate_relpath(rel) for rel in paths]
    candidate_hashes = validate_candidate(candidate, root, normalized)
    syntax_check(candidate, normalized)

    with promotion_lock(state):
        pre = run_prechange(prechange, root, state)
        mapping_sha = mapping_from_recovery_status(root, state)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "candidate": str(candidate),
                "paths": normalized,
                "candidate_hashes": candidate_hashes,
                "prechange_snapshot": pre.get("snapshot_id"),
                "mapping_sha256": mapping_sha,
            }

        before = capture_lkg(
            root,
            state,
            "pre-transactional-promotion: PRECHANGE_GREEN; "
            f"candidate={candidate.name}; paths={','.join(normalized)}",
        )
        tx_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
        tx_root = state / "promotion-transactions" / tx_id
        log = state / "promotion.log"
        runtime_backup: Path | None = None
        source_promoted = False
        write_json_line(
            log,
            "promotion_started",
            transaction_id=tx_id,
            candidate=str(candidate),
            paths=normalized,
            mapping_sha256=mapping_sha,
            lkg_snapshot=before.get("snapshot_id"),
        )
        try:
            deployed_hashes = transactional_replace(candidate, root, normalized, tx_root)
            source_promoted = True
            service_restart()

            if "firefox-extension/background.js" in normalized:
                runtime_backup = sync_firefox_runtime(
                    root, runtime_extension, reload_helper, tx_root
                )

            post = run_postdeploy(
                postdeploy,
                root=root,
                expected_mapping_sha=mapping_sha,
                require_worker_read_model=require_worker_read_model,
                require_incidents=require_incidents,
            )
            after = capture_lkg(
                root,
                state,
                "transactional promotion green: POSTDEPLOY_GREEN; "
                f"tx={tx_id}; mapping={mapping_sha}",
            )
            write_json_line(
                log,
                "promotion_succeeded",
                transaction_id=tx_id,
                deployed_hashes=deployed_hashes,
                postdeploy=post,
                new_lkg=after.get("snapshot_id"),
            )
            return {
                "ok": True,
                "transaction_id": tx_id,
                "paths": normalized,
                "deployed_hashes": deployed_hashes,
                "mapping_sha256": mapping_sha,
                "postdeploy": post,
                "new_lkg": after.get("snapshot_id"),
            }
        except Exception as exc:
            rollback_error = None
            if source_promoted:
                try:
                    rollback_lkg(root, state)
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
            try:
                restore_firefox_runtime(runtime_extension, runtime_backup, reload_helper)
            except Exception as runtime_exc:
                rollback_error = (
                    (rollback_error + "; " if rollback_error else "")
                    + f"Firefox runtime restore failed: {runtime_exc}"
                )
            write_json_line(
                log,
                "promotion_failed",
                transaction_id=tx_id,
                error=str(exc),
                rollback_error=rollback_error,
            )
            if rollback_error:
                raise PromotionError(
                    f"promotion failed and rollback was not fully clean: {exc}; {rollback_error}"
                ) from exc
            raise PromotionError(f"promotion failed; LKG restored: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded transactional zCloud promotion")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--prechange", type=Path, default=DEFAULT_PRECHANGE)
    parser.add_argument("--postdeploy", type=Path, default=DEFAULT_POSTDEPLOY)
    parser.add_argument("--runtime-extension", type=Path, default=DEFAULT_RUNTIME_EXTENSION)
    parser.add_argument("--reload-helper", type=Path, default=DEFAULT_RELOAD_HELPER)
    parser.add_argument("--require-worker-read-model", action="store_true")
    parser.add_argument("--require-incidents", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = promote(
            args.candidate,
            args.root,
            args.state,
            args.paths or list(DEFAULT_PATHS),
            prechange=args.prechange,
            postdeploy=args.postdeploy,
            runtime_extension=args.runtime_extension,
            reload_helper=args.reload_helper,
            require_worker_read_model=args.require_worker_read_model,
            require_incidents=args.require_incidents,
            dry_run=args.dry_run,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("PROMOTION_DRY_RUN_GREEN" if result.get("dry_run") else "PROMOTION_GREEN")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PromotionError as exc:
        print(f"PROMOTION_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
