#!/usr/bin/env python3
"""VPS-native zCloud autodeployer.

Polls public GitHub for a new main commit, requires the zCloud regression
workflow to be green for that exact SHA, briefly holds new AI-cycle dispatch,
lets already-running ChatGPT cycles finish, and then promotes the validated
candidate transactionally. No ChatGPT trigger or repository self-hosted runner
is required.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
STATE_DIR = Path(os.environ.get("ZCLOUD_STATE_DIR", str(Path.home() / ".local/state/zcloud")))
STATE_FILE = STATE_DIR / "autodeploy.json"
HOLD_FILE = STATE_DIR / "deploy-hold.json"
REPO_URL = os.environ.get("ZCLOUD_REPO_URL", "https://github.com/Zennay/zCloud.git")
ACTIONS_API = "https://api.github.com/repos/Zennay/zCloud/actions/runs"
WORKFLOW_NAME = "zCloud regression smoke"
MAX_IDLE_WAIT_SECONDS = int(os.environ.get("ZCLOUD_DEPLOY_IDLE_TIMEOUT", "900"))
IDLE_STABLE_SECONDS = 8

GROUPS = (
    ("config", ("projects.json", "project-layout.json", "resource-policy.json")),
    ("backend", ("server.py", "enhancements.py", "autonomy-policy.json")),
    ("browser", (
        "firefox-extension/background.js",
        "firefox-extension/recovery.js",
        "firefox-extension/manifest.json",
    )),
    ("ui", (
        "public/app.js",
        "public/index.html",
        "public/style.css",
        "public/enhancements.js",
        "public/enhancements.css",
        "public/manifest.webmanifest",
    )),
    ("tools-a", (
        "scripts/zcloud_config_validate.py",
        "scripts/zcloud_healthcheck.py",
        "scripts/zcloud_postdeploy_canary.py",
        "scripts/zcloud_prechange_guard.py",
        "scripts/zcloud_recovery.py",
    )),
    ("tools-b", (
        "scripts/zcloud_reload_extension.mjs",
        "scripts/zcloud_transactional_promote.py",
        "scripts/zcloud-self-heal.sh",
        "scripts/birds_eye_review.sh",
        "scripts/worker_scaling_report.py",
    )),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(event: str, **fields) -> None:
    row = {"time": utc_now(), "event": event, **fields}
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def run(args: list[str], *, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(x) for x in args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(map(str,args))}\n{proc.stdout[-4000:]}")
    return proc


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main_sha() -> str:
    proc = run(["git", "ls-remote", REPO_URL, "refs/heads/main"])
    sha = (proc.stdout.strip().split() or [""])[0]
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha.lower()):
        raise RuntimeError(f"invalid main SHA from remote: {sha!r}")
    return sha


def exact_regression_green(sha: str) -> tuple[bool, str]:
    query = urllib.parse.urlencode({
        "head_sha": sha,
        "event": "push",
        "status": "completed",
        "per_page": 30,
    })
    req = urllib.request.Request(
        ACTIONS_API + "?" + query,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "zcloud-vps-autodeploy/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, f"actions_lookup_failed:{type(exc).__name__}"
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return False, "actions_payload_invalid"
    matching = [
        item for item in runs
        if item.get("name") == WORKFLOW_NAME
        and item.get("head_sha") == sha
        and item.get("head_branch") == "main"
    ]
    if not matching:
        return False, "regression_not_completed"
    matching.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    conclusion = str(matching[0].get("conclusion") or "")
    return conclusion == "success", f"regression_{conclusion or 'unknown'}"


def ensure_candidate(sha: str) -> tuple[Path, Path]:
    run(["git", "-C", str(ROOT), "fetch", "--prune", "origin", "main"])
    run(["git", "-C", str(ROOT), "cat-file", "-e", sha + "^{commit}"])
    temp_root = Path(tempfile.mkdtemp(prefix="zcloud-autodeploy-", dir="/tmp"))
    candidate = temp_root / "candidate"
    run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(candidate), sha])
    actual = run(["git", "-C", str(candidate), "rev-parse", "HEAD"]).stdout.strip()
    if actual != sha:
        raise RuntimeError(f"candidate SHA mismatch: expected {sha}, got {actual}")
    return temp_root, candidate


def cleanup_candidate(temp_root: Path | None, candidate: Path | None) -> None:
    if candidate:
        run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(candidate)], check=False)
    if temp_root:
        shutil.rmtree(temp_root, ignore_errors=True)


@contextmanager
def deployment_hold(sha: str):
    payload = {"pid": os.getpid(), "sha": sha, "started_at": utc_now()}
    write_json_atomic(HOLD_FILE, payload)
    try:
        yield
    finally:
        try:
            current = json.loads(HOLD_FILE.read_text(encoding="utf-8")) if HOLD_FILE.exists() else {}
            if int(current.get("pid") or -1) == os.getpid():
                HOLD_FILE.unlink(missing_ok=True)
        except Exception:
            HOLD_FILE.unlink(missing_ok=True)


def worker_busy(conn: sqlite3.Connection, project_id: str, worker_slot: int) -> bool:
    latest = conn.execute(
        "SELECT id,generating,sending FROM runner_events "
        "WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 1",
        (project_id, worker_slot),
    ).fetchone()
    if not latest:
        return False
    last_start = conn.execute(
        "SELECT MAX(id) FROM runner_events WHERE project_id=? AND worker_slot=? AND event='generation-started'",
        (project_id, worker_slot),
    ).fetchone()[0] or 0
    last_finish = conn.execute(
        "SELECT MAX(id) FROM runner_events WHERE project_id=? AND worker_slot=? AND event='generation-finished'",
        (project_id, worker_slot),
    ).fetchone()[0] or 0
    return bool(latest["generating"]) or bool(latest["sending"]) or last_start > last_finish


def wait_for_idle() -> list[str]:
    db = ROOT / "history.db"
    deadline = time.time() + MAX_IDLE_WAIT_SECONDS
    stable_since: float | None = None
    last_keys: list[str] = []
    while time.time() < deadline:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_global_slots'"
        ).fetchone()
        slots = list(conn.execute(
            "SELECT slot,project_id,worker_slot FROM ai_global_slots ORDER BY slot"
        )) if table else []
        busy = []
        last_keys = []
        for row in slots:
            project_id = str(row["project_id"])
            worker_slot = max(1, int(row["worker_slot"] or 1))
            last_keys.append(f"{project_id}::w{worker_slot}")
            busy.append(worker_busy(conn, project_id, worker_slot))
        conn.close()
        if len(slots) <= 2 and not any(busy):
            stable_since = stable_since or time.time()
            if time.time() - stable_since >= IDLE_STABLE_SECONDS:
                return last_keys
        else:
            stable_since = None
        time.sleep(2)
    raise RuntimeError(f"AI slots did not become idle before deploy timeout: {last_keys}")


def file_changed(candidate: Path, rel: str) -> bool:
    new = candidate / rel
    old = ROOT / rel
    if not new.exists():
        return False
    if not old.exists():
        return True
    return hashlib.sha256(new.read_bytes()).digest() != hashlib.sha256(old.read_bytes()).digest()


def promote(candidate: Path, label: str, relpaths: tuple[str, ...]) -> list[str]:
    changed = [rel for rel in relpaths if file_changed(candidate, rel)]
    if not changed:
        return []
    promoter = candidate / "scripts/zcloud_transactional_promote.py"
    args = [
        sys.executable, str(promoter),
        "--candidate", str(candidate),
        "--root", str(ROOT),
        "--state", str(Path.home() / ".local/state/zcloud/recovery"),
        "--prechange", str(candidate / "scripts/zcloud_prechange_guard.py"),
        "--postdeploy", str(candidate / "scripts/zcloud_postdeploy_canary.py"),
        "--config-validator", str(candidate / "scripts/zcloud_config_validate.py"),
        "--reload-helper", str(candidate / "scripts/zcloud_reload_extension.mjs"),
        "--actor", "zcloud-vps-autodeploy",
        "--json",
    ]
    for rel in changed:
        args += ["--path", rel]
    proc = run(args, check=False)
    if proc.returncode:
        raise RuntimeError(f"{label} promotion failed ({proc.returncode}): {proc.stdout[-5000:]}")
    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:
        raise RuntimeError(f"{label} promotion returned invalid JSON: {exc}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"{label} promotion did not report ok")
    log("promotion_green", group=label, paths=changed, transaction_id=payload.get("transaction_id"))
    return changed


def install_runtime_assets(candidate: Path) -> None:
    # The timer executes an installed copy so it can update the live checkout safely.
    local_bin = Path.home() / ".local/bin"
    units = Path.home() / ".config/systemd/user"
    local_bin.mkdir(parents=True, exist_ok=True)
    units.mkdir(parents=True, exist_ok=True)

    source = candidate / "scripts/zcloud_autodeploy.py"
    if source.exists():
        target = local_bin / "zcloud-autodeploy.py"
        staged = target.with_name(target.name + f".new-{os.getpid()}")
        shutil.copy2(source, staged)
        staged.chmod(0o755)
        os.replace(staged, target)

    changed_units = False
    for name in ("zcloud-autodeploy.service", "zcloud-autodeploy.timer"):
        source = candidate / "deploy" / name
        if not source.exists():
            continue
        target = units / name
        new_bytes = source.read_bytes()
        if target.exists() and target.read_bytes() == new_bytes:
            continue
        staged = target.with_name(target.name + f".new-{os.getpid()}")
        staged.write_bytes(new_bytes)
        os.replace(staged, target)
        changed_units = True
    if changed_units:
        run(["systemctl", "--user", "daemon-reload"])


def deploy_once() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = main_sha()
    state = load_state()
    if state.get("last_deployed_sha") == target:
        log("already_current", sha=target)
        return 0

    green, reason = exact_regression_green(target)
    if not green:
        log("not_deployable_yet", sha=target, reason=reason)
        return 0

    temp_root: Path | None = None
    candidate: Path | None = None
    try:
        temp_root, candidate = ensure_candidate(target)
        with deployment_hold(target):
            slots = wait_for_idle()
            log("maintenance_window_acquired", sha=target, slots=slots)
            promoted: list[str] = []
            for label, paths in GROUPS:
                promoted.extend(promote(candidate, label, paths))
            install_runtime_assets(candidate)
            write_json_atomic(STATE_FILE, {
                "last_deployed_sha": target,
                "deployed_at": utc_now(),
                "promoted_paths": promoted,
            })
            log("deploy_green", sha=target, promoted_paths=promoted)
        return 0
    finally:
        cleanup_candidate(temp_root, candidate)


def main() -> int:
    try:
        return deploy_once()
    except Exception as exc:
        log("deploy_failed", error=str(exc)[:1000])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
