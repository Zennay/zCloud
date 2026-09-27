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
  6. read-only post-deploy canary with strict mapping preservation or an explicit extension-only contract
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
import sqlite3
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
DEFAULT_CONFIG_VALIDATOR = Path(os.environ.get(
    "ZCLOUD_CONFIG_VALIDATOR",
    str(Path.home() / ".local/bin/zcloud-config-validate"),
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

CREATABLE_PATHS = {
    "firefox-extension/recovery.js",
}

AUDITED_CONFIG_PATHS = {
    "projects.json": ("project.catalog", "portfolio"),
    "project-layout.json": ("project.layout", "portfolio"),
    "resource-policy.json": ("resource.policy", "portfolio"),
}

HIGH_BLAST_FLAG = "high_blast_radius_promotion"


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
        if not target.exists():
            if rel not in CREATABLE_PATHS:
                raise PromotionError(f"live target file missing: {rel}")
        elif not target.is_file():
            raise PromotionError(f"live target is not a file: {rel}")
        hashes[rel] = sha256_file(source)
    return hashes


def config_changes(candidate: Path, root: Path, paths: list[str]) -> list[dict]:
    changes = []
    for rel in paths:
        if rel not in AUDITED_CONFIG_PATHS:
            continue
        old_value = json.loads((root / rel).read_text(encoding="utf-8"))
        new_value = json.loads((candidate / rel).read_text(encoding="utf-8"))
        if old_value == new_value:
            continue
        config_key, target = AUDITED_CONFIG_PATHS[rel]
        changes.append({
            "path": rel,
            "config_key": config_key,
            "target": target,
            "old_value": old_value,
            "new_value": new_value,
        })
    return changes


def write_config_audit(
    db_path: Path,
    changes: list[dict],
    *,
    actor: str,
    result: str,
    transaction_id: str,
    detail: str = "",
) -> None:
    if not changes:
        return
    with sqlite3.connect(db_path, timeout=4) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_audit'"
        ).fetchone()
        if not exists:
            raise PromotionError("config_audit table missing; refusing unaudited config promotion")
        for change in changes:
            conn.execute(
                "INSERT INTO config_audit("
                "ts,actor,config_key,target,old_value_json,new_value_json,result,detail"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    utc_now(),
                    str(actor or "transactional-promote")[:128],
                    change["config_key"],
                    change["target"],
                    json.dumps(change["old_value"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    json.dumps(change["new_value"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    result,
                    (f"tx={transaction_id}; path={change['path']}; {detail}").strip()[:500],
                ),
            )


def promotion_blast_radius(paths: list[str]) -> dict:
    normalized=[validate_relpath(rel) for rel in paths]
    planes=set()
    for rel in normalized:
        if rel in AUDITED_CONFIG_PATHS:
            planes.add("config")
        elif rel.startswith("firefox-extension/"):
            planes.add("browser")
        elif rel.startswith("public/"):
            planes.add("ui")
        elif rel in ("server.py","enhancements.py") or rel.startswith(("scripts/","deploy/")):
            planes.add("service")
        else:
            planes.add("other")
    reasons=[]
    if "service" in planes and "browser" in planes:
        reasons.append("service+browser")
    if len(planes)>=3:
        reasons.append("three_or_more_control_planes")
    if len(normalized)>=6:
        reasons.append("six_or_more_files")
    return {
        "high":bool(reasons),
        "planes":sorted(planes),
        "paths":normalized,
        "reasons":reasons,
    }


def feature_flag_state(db_path: Path, name: str) -> dict:
    if not db_path.exists():
        return {"name":name,"enabled":False,"effective":False,"reason":"state_store_missing"}
    try:
        conn=sqlite3.connect(f"file:{db_path}?mode=ro",uri=True,timeout=2)
        conn.row_factory=sqlite3.Row
        table=conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feature_flags'"
        ).fetchone()
        if not table:
            conn.close()
            return {"name":name,"enabled":False,"effective":False,"reason":"table_missing"}
        row=conn.execute(
            "SELECT enabled,expires_at,updated_at,actor FROM feature_flags WHERE name=?",
            (name,),
        ).fetchone()
        conn.close()
    except Exception as exc:
        return {"name":name,"enabled":False,"effective":False,"reason":f"lookup_failed:{exc}"}
    if not row:
        return {"name":name,"enabled":False,"effective":False,"reason":"flag_missing"}
    enabled=bool(row["enabled"])
    expires_at=row["expires_at"]
    expired=False
    if enabled and expires_at:
        try:
            expired=datetime.fromisoformat(str(expires_at)).astimezone(timezone.utc) <= datetime.now(timezone.utc)
        except Exception:
            expired=True
    return {
        "name":name,
        "enabled":enabled,
        "effective":enabled and not expired,
        "expires_at":expires_at,
        "updated_at":row["updated_at"],
        "actor":row["actor"],
        "reason":"expired" if expired else ("enabled" if enabled else "disabled"),
    }


def enforce_blast_radius_gate(db_path: Path, paths: list[str]) -> dict:
    blast=promotion_blast_radius(paths)
    state=feature_flag_state(db_path,HIGH_BLAST_FLAG) if blast["high"] else {
        "name":HIGH_BLAST_FLAG,
        "enabled":False,
        "effective":False,
        "reason":"not_required",
    }
    result={"blast_radius":blast,"feature_flag":state}
    if blast["high"] and not state.get("effective"):
        reasons=",".join(blast["reasons"]) or "high_blast_radius"
        raise PromotionError(
            f"high-blast promotion blocked ({reasons}); enable {HIGH_BLAST_FLAG} temporarily"
        )
    return result


def syntax_check(candidate: Path, paths: list[str]) -> None:
    python_files = [str(candidate / rel) for rel in paths if rel.endswith(".py")]
    js_files = [str(candidate / rel) for rel in paths if rel.endswith((".js", ".mjs"))]
    if python_files:
        run([sys.executable, "-m", "py_compile", *python_files])
    for js in js_files:
        run(["node", "--check", js])
    for rel in paths:
        if rel.endswith(".json"):
            try:
                json.loads((candidate / rel).read_text(encoding="utf-8"))
            except Exception as exc:
                raise PromotionError(f"invalid JSON candidate {rel}: {exc}") from exc


def needs_service_restart(paths: list[str]) -> bool:
    return any(not str(rel).startswith("firefox-extension/") for rel in paths)


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


def mapping_rows(db_path: Path) -> dict:
    """Return the durable runner mapping rows used by extension-only validation."""
    if not db_path.exists():
        raise PromotionError("history.db missing while reading runner mapping")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        targets = [
            dict(row)
            for row in conn.execute(
                "SELECT project_id,active,worker_count,conversation_id "
                "FROM runner_targets ORDER BY project_id"
            )
        ]
        workers = [
            dict(row)
            for row in conn.execute(
                "SELECT project_id,worker_slot,conversation_id "
                "FROM runner_workers ORDER BY project_id,worker_slot"
            )
        ]
        conn.close()
    except Exception as exc:
        raise PromotionError(f"runner mapping rows unavailable: {exc}") from exc
    return {"targets": targets, "workers": workers}


def validate_mapping_extension(before: dict, after: dict) -> dict:
    """Allow only additive mapping rows; every pre-existing row must be byte-equivalent."""
    def indexed(rows: list[dict], keys: tuple[str, ...], label: str) -> dict[tuple, dict]:
        result = {}
        for row in rows:
            key = tuple(row.get(name) for name in keys)
            if key in result:
                raise PromotionError(f"duplicate {label} mapping key: {key}")
            result[key] = row
        return result

    before_targets = indexed(before.get("targets") or [], ("project_id",), "target")
    after_targets = indexed(after.get("targets") or [], ("project_id",), "target")
    before_workers = indexed(
        before.get("workers") or [], ("project_id", "worker_slot"), "worker"
    )
    after_workers = indexed(
        after.get("workers") or [], ("project_id", "worker_slot"), "worker"
    )

    changed = []
    for label, old, new in (
        ("target", before_targets, after_targets),
        ("worker", before_workers, after_workers),
    ):
        for key, row in old.items():
            if key not in new:
                changed.append({"kind": label, "key": list(key), "reason": "removed"})
            elif new[key] != row:
                changed.append({
                    "kind": label,
                    "key": list(key),
                    "reason": "changed",
                    "before": row,
                    "after": new[key],
                })
    if changed:
        raise PromotionError(
            "mapping extension modified existing mapping rows: "
            + json.dumps(changed[:5], ensure_ascii=False, sort_keys=True)
        )

    added_targets = sorted(
        key[0] for key in after_targets.keys() - before_targets.keys()
    )
    added_workers = sorted(
        [list(key) for key in after_workers.keys() - before_workers.keys()]
    )
    return {
        "ok": True,
        "policy": "extension_only",
        "targets_before": len(before_targets),
        "targets_after": len(after_targets),
        "workers_before": len(before_workers),
        "workers_after": len(after_workers),
        "added_targets": added_targets,
        "added_workers": added_workers,
    }


def validate_mapping_extension_scope(paths: list[str], enabled: bool) -> None:
    if not enabled:
        return
    allowed = {"projects.json", "project-layout.json"}
    selected = set(paths)
    if not selected or not selected.issubset(allowed):
        raise PromotionError(
            "--allow-mapping-extension is restricted to projects.json/project-layout.json"
        )


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


def run_config_validation(
    validator: Path,
    *,
    candidate: Path,
    root: Path,
    paths: list[str],
) -> dict:
    selected = set(paths)

    def effective(rel: str) -> Path:
        return candidate / rel if rel in selected else root / rel

    args = [
        str(validator),
        "--projects", str(effective("projects.json")),
        "--layout", str(effective("project-layout.json")),
        "--resource-policy", str(effective("resource-policy.json")),
        "--server", str(effective("server.py")),
        "--enhancements", str(effective("enhancements.py")),
        "--db", str(root / "history.db"),
        "--json",
    ]
    proc = run(args, check=False)
    payload = parse_json_output(proc, "config schema validator")
    if proc.returncode or not payload.get("ok"):
        detail = "; ".join(str(x) for x in (payload.get("errors") or [])[:5])
        raise PromotionError(
            "config schema validation is not green"
            + (f": {detail}" if detail else "")
        )
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
    expected_mapping_sha: str | None,
    require_worker_read_model: bool,
    require_incidents: bool,
) -> dict:
    args = [
        str(postdeploy),
        "--root", str(root),
        "--db", str(root / "history.db"),
        "--json",
    ]
    if expected_mapping_sha:
        args.extend(["--expect-mapping-sha", expected_mapping_sha])
    if require_worker_read_model:
        args.append("--require-worker-read-model")
    if require_incidents:
        args.append("--require-incidents")
    proc = run(args, check=False)
    payload = parse_json_output(proc, "post-deploy canary")
    if proc.returncode or not payload.get("ok"):
        detail = "; ".join(
            f"{item.get('name')}={item.get('detail')}"
            for item in (payload.get("checks") or [])
            if not item.get("ok")
        )
        raise PromotionError(
            "post-deploy canary is not green" + (f": {detail}" if detail else "")
        )
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
            if dst.exists():
                shutil.copy2(dst, old)
            os.replace(staged / rel, dst)
            completed.append(rel)
            if fail_after is not None and len(completed) >= fail_after:
                raise PromotionError("simulated promotion failure")
    except Exception:
        for rel in reversed(completed):
            old = backup / rel
            dst = root / rel
            if old.exists():
                os.replace(old, dst)
            elif dst.exists():
                dst.unlink()
        raise

    return {rel: sha256_file(root / rel) for rel in normalized}


def sync_firefox_runtime(
    root: Path,
    runtime_extension: Path,
    reload_helper: Path,
    tx_root: Path,
    extension_paths: list[str],
) -> dict[str, Path | None]:
    runtime_root = runtime_extension.parent
    selected = [
        validate_relpath(rel) for rel in extension_paths
        if validate_relpath(rel).startswith("firefox-extension/")
    ]
    if not selected:
        return {}

    backup_root = tx_root / "firefox-runtime-backup"
    backups: dict[str, Path | None] = {}
    try:
        for rel in selected:
            source = root / rel
            relative = Path(rel).relative_to("firefox-extension")
            runtime = runtime_root / relative
            backup = backup_root / relative
            runtime.parent.mkdir(parents=True, exist_ok=True)
            if runtime.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(runtime, backup)
                backups[rel] = backup
            else:
                backups[rel] = None
            stage = runtime.with_name(runtime.name + f".zcloud-new-{os.getpid()}")
            shutil.copy2(source, stage)
            os.replace(stage, runtime)

        if not reload_helper.exists():
            raise PromotionError(f"Firefox reload helper missing: {reload_helper}")
        run(["node", str(reload_helper)])

        for rel in selected:
            source = root / rel
            runtime = runtime_root / Path(rel).relative_to("firefox-extension")
            if sha256_file(source) != sha256_file(runtime):
                raise PromotionError(f"Firefox source/runtime mismatch after sync: {rel}")
        return backups
    except Exception:
        restore_firefox_runtime(runtime_extension, backups, reload_helper)
        raise


def restore_firefox_runtime(
    runtime_extension: Path,
    runtime_backups: dict[str, Path | None],
    reload_helper: Path,
) -> None:
    if not runtime_backups:
        return
    runtime_root = runtime_extension.parent
    for rel, backup in runtime_backups.items():
        runtime = runtime_root / Path(rel).relative_to("firefox-extension")
        if backup is None:
            if runtime.exists():
                runtime.unlink()
            continue
        stage = runtime.with_name(runtime.name + f".zcloud-rollback-{os.getpid()}")
        shutil.copy2(backup, stage)
        os.replace(stage, runtime)
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
    config_validator: Path = DEFAULT_CONFIG_VALIDATOR,
    runtime_extension: Path = DEFAULT_RUNTIME_EXTENSION,
    reload_helper: Path = DEFAULT_RELOAD_HELPER,
    require_worker_read_model: bool = False,
    require_incidents: bool = False,
    allow_mapping_extension: bool = False,
    dry_run: bool = False,
    actor: str = "transactional-promote",
) -> dict:
    candidate = candidate.resolve()
    root = root.resolve()
    state = state.resolve()
    normalized = [validate_relpath(rel) for rel in paths]
    validate_mapping_extension_scope(normalized, allow_mapping_extension)
    candidate_hashes = validate_candidate(candidate, root, normalized)
    syntax_check(candidate, normalized)
    feature_gate = enforce_blast_radius_gate(root / "history.db", normalized)
    pending_config_changes = config_changes(candidate, root, normalized)
    config_validation = run_config_validation(
        config_validator,
        candidate=candidate,
        root=root,
        paths=normalized,
    )

    with promotion_lock(state):
        pre = run_prechange(prechange, root, state)
        mapping_sha = mapping_from_recovery_status(root, state)
        mapping_before = (
            mapping_rows(root / "history.db") if allow_mapping_extension else None
        )
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "candidate": str(candidate),
                "paths": normalized,
                "candidate_hashes": candidate_hashes,
                "config_validation": config_validation,
                "feature_gate": feature_gate,
                "config_changes": pending_config_changes,
                "prechange_snapshot": pre.get("snapshot_id"),
                "mapping_sha256": mapping_sha,
                "mapping_policy": (
                    "extension_only" if allow_mapping_extension else "unchanged"
                ),
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
        runtime_backups: dict[str, Path | None] = {}
        created_source_paths = [rel for rel in normalized if not (root / rel).exists()]
        source_promoted = False
        write_json_line(
            log,
            "promotion_started",
            transaction_id=tx_id,
            candidate=str(candidate),
            paths=normalized,
            mapping_sha256=mapping_sha,
            mapping_policy=(
                "extension_only" if allow_mapping_extension else "unchanged"
            ),
            lkg_snapshot=before.get("snapshot_id"),
            feature_gate=feature_gate,
        )
        try:
            deployed_hashes = transactional_replace(candidate, root, normalized, tx_root)
            source_promoted = True
            if needs_service_restart(normalized):
                service_restart()

            extension_paths = [
                rel for rel in normalized if rel.startswith("firefox-extension/")
            ]
            if extension_paths:
                runtime_backups = sync_firefox_runtime(
                    root, runtime_extension, reload_helper, tx_root, extension_paths
                )

            post = run_postdeploy(
                postdeploy,
                root=root,
                expected_mapping_sha=(
                    None if allow_mapping_extension else mapping_sha
                ),
                require_worker_read_model=require_worker_read_model,
                require_incidents=require_incidents,
            )
            mapping_contract = None
            if allow_mapping_extension:
                assert mapping_before is not None
                mapping_contract = validate_mapping_extension(
                    mapping_before,
                    mapping_rows(root / "history.db"),
                )
            mapping_after_sha = ((post.get("mapping") or {}).get("sha256"))
            write_config_audit(
                root / "history.db",
                pending_config_changes,
                actor=actor,
                result="succeeded",
                transaction_id=tx_id,
                detail="POSTDEPLOY_GREEN",
            )
            after = capture_lkg(
                root,
                state,
                "transactional promotion green: POSTDEPLOY_GREEN; "
                f"tx={tx_id}; mapping_policy="
                f"{'extension_only' if allow_mapping_extension else 'unchanged'}; "
                f"mapping_before={mapping_sha}; mapping_after={mapping_after_sha}",
            )
            write_json_line(
                log,
                "promotion_succeeded",
                transaction_id=tx_id,
                deployed_hashes=deployed_hashes,
                postdeploy=post,
                mapping_contract=mapping_contract,
                new_lkg=after.get("snapshot_id"),
            )
            return {
                "ok": True,
                "transaction_id": tx_id,
                "paths": normalized,
                "deployed_hashes": deployed_hashes,
                "mapping_sha256_before": mapping_sha,
                "mapping_sha256_after": mapping_after_sha,
                "mapping_policy": (
                    "extension_only" if allow_mapping_extension else "unchanged"
                ),
                "mapping_contract": mapping_contract,
                "feature_gate": feature_gate,
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
            for rel in created_source_paths:
                created = root / rel
                if created.exists():
                    try:
                        created.unlink()
                    except Exception as cleanup_exc:
                        rollback_error = (
                            (rollback_error + "; " if rollback_error else "")
                            + f"created source cleanup failed for {rel}: {cleanup_exc}"
                        )
            try:
                restore_firefox_runtime(runtime_extension, runtime_backups, reload_helper)
            except Exception as runtime_exc:
                rollback_error = (
                    (rollback_error + "; " if rollback_error else "")
                    + f"Firefox runtime restore failed: {runtime_exc}"
                )
            try:
                write_config_audit(
                    root / "history.db",
                    pending_config_changes,
                    actor=actor,
                    result="failed",
                    transaction_id=tx_id,
                    detail=str(exc)[:300],
                )
            except Exception as audit_exc:
                rollback_error = (
                    (rollback_error + "; " if rollback_error else "")
                    + f"config audit failed: {audit_exc}"
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
    parser.add_argument("--config-validator", type=Path, default=DEFAULT_CONFIG_VALIDATOR)
    parser.add_argument("--runtime-extension", type=Path, default=DEFAULT_RUNTIME_EXTENSION)
    parser.add_argument("--reload-helper", type=Path, default=DEFAULT_RELOAD_HELPER)
    parser.add_argument("--require-worker-read-model", action="store_true")
    parser.add_argument("--require-incidents", action="store_true")
    parser.add_argument(
        "--allow-mapping-extension",
        action="store_true",
        help=(
            "Allow only additive runner mapping rows; restricted to "
            "projects.json/project-layout.json promotions"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--actor", default="transactional-promote")
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
            config_validator=args.config_validator,
            runtime_extension=args.runtime_extension,
            reload_helper=args.reload_helper,
            require_worker_read_model=args.require_worker_read_model,
            require_incidents=args.require_incidents,
            allow_mapping_extension=args.allow_mapping_extension,
            dry_run=args.dry_run,
            actor=args.actor,
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
