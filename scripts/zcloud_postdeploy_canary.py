#!/usr/bin/env python3
"""Read-only post-deploy canary for the zCloud control plane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("ZCLOUD_ROOT", "/home/ubuntu/zennay-cloud"))
DB = Path(os.environ.get("ZCLOUD_DB", str(ROOT / "history.db")))
BASE_URL = os.environ.get("ZCLOUD_BASE_URL", "http://127.0.0.1:8765")
RUNTIME_EXTENSION = Path(os.environ.get(
    "ZCLOUD_FIREFOX_RUNTIME_EXTENSION",
    str(Path.home() / "snap/firefox/common/chatgpt-project-extension/background.js"),
))
LEGACY_FIREFOX_DISABLE_DROPIN = Path(os.environ.get(
    "ZCLOUD_LEGACY_FIREFOX_DISABLE_DROPIN",
    str(Path.home() / ".config/systemd/user/chatgpt-firefox.service.d/10-legacy-disabled.conf"),
))
STATUS_REQUEST_TIMEOUT_SECONDS = float(
    os.environ.get("ZCLOUD_STATUS_REQUEST_TIMEOUT_SECONDS", "40")
)
STATUS_READINESS_SECONDS = float(
    os.environ.get("ZCLOUD_STATUS_READINESS_SECONDS", "90")
)


def legacy_firefox_intentionally_disabled(path: Path = LEGACY_FIREFOX_DISABLE_DROPIN) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    lowered = text.lower()
    return "execcondition=/bin/false" in lowered and "violentmonkey only" in lowered


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def firefox_runtime_parity(root: Path, runtime_extension: Path) -> dict:
    runtime_root = runtime_extension.parent
    names = ["background.js", "manifest.json", "recovery.js"]
    checked = []
    mismatches = []
    for name in names:
        source = root / "firefox-extension" / name
        if not source.exists():
            continue
        runtime = runtime_root / name
        checked.append(name)
        if not runtime.exists():
            mismatches.append({"file": name, "reason": "runtime missing"})
            continue
        source_hash = sha256_file(source)
        runtime_hash = sha256_file(runtime)
        if source_hash != runtime_hash:
            mismatches.append({
                "file": name,
                "source": source_hash,
                "runtime": runtime_hash,
            })
    return {
        "ok": bool(checked) and not mismatches,
        "checked": checked,
        "mismatches": mismatches,
    }


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


def http_json(url: str, timeout: float = 8.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"non-object JSON: {url}")
        return data


def http_json_ready(
    url: str,
    timeout: float = STATUS_REQUEST_TIMEOUT_SECONDS,
    readiness_seconds: float = STATUS_READINESS_SECONDS,
    retry_interval: float = 0.5,
    *,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> dict:
    """Wait briefly for a just-restarted API endpoint to become ready.

    The deploy already proves that the HTTP server is listening before this
    canary runs. /api/status additionally aggregates runner and Firefox runtime
    state, including bounded systemd probes that can legitimately outlive the
    generic 8-second transport timeout during restart/rebind. Give each deep
    status request enough time to finish while keeping the same 90-second
    overall readiness deadline. Persistent failures still raise and therefore
    keep the promotion fail-closed.
    """
    readiness_budget = max(0.0, float(readiness_seconds))
    deadline = monotonic_fn() + readiness_budget
    request_timeout = max(0.001, min(float(timeout), max(0.001, readiness_budget)))
    last_error = None
    while True:
        try:
            return http_json(url, timeout=request_timeout)
        except Exception as exc:
            last_error = exc
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                raise last_error
            sleep_for = min(max(0.05, float(retry_interval)), remaining)
            sleep_fn(sleep_for)
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                raise last_error
            request_timeout = max(0.001, min(float(timeout), remaining))


def http_ok(url: str, timeout: float = 8.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def mapping_fingerprint(db_path: Path) -> dict:
    if not db_path.exists():
        return {"available": False, "reason": "history.db missing"}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        targets = [dict(row) for row in conn.execute(
            "SELECT project_id,conversation_id "
            "FROM runner_targets ORDER BY project_id"
        )]
        workers = [dict(row) for row in conn.execute(
            "SELECT project_id,worker_slot,conversation_id "
            "FROM runner_workers ORDER BY project_id,worker_slot"
        )]
        try:
            dynamic_row = conn.execute(
                "SELECT value,updated_at,actor FROM runtime_settings "
                "WHERE key='dynamic_worker_limit'"
            ).fetchone()
        except sqlite3.OperationalError:
            dynamic_row = None
        dynamic_worker_limit = None
        if dynamic_row is not None:
            try:
                dynamic_worker_limit = int(dynamic_row["value"])
            except (TypeError, ValueError):
                dynamic_worker_limit = None
        conn.close()
        payload = json.dumps(
            {"targets": targets, "workers": workers},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return {
            "available": True,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "targets": len(targets),
            "workers": len(workers),
            "dynamic_worker_limit": dynamic_worker_limit,
            "dynamic_worker_setting": dict(dynamic_row) if dynamic_row is not None else None,
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}


def evaluate(
    status: dict,
    targets_payload: dict,
    mapping: dict,
    *,
    services: dict[str, bool],
    static_assets_ok: bool,
    source_runtime_match: bool,
    legacy_firefox_disabled: bool = False,
    expected_mapping_sha: str | None = None,
    require_worker_read_model: bool = False,
    require_incidents: bool = False,
) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("zcloud_service", services.get("zcloud") is True, services.get("zcloud"))
    firefox_service_active = services.get("firefox") is True
    add(
        "firefox_service",
        firefox_service_active or legacy_firefox_disabled,
        {
            "active": firefox_service_active,
            "legacy_violentmonkey_only": legacy_firefox_disabled,
        },
    )
    add("static_assets", static_assets_ok, "app.js + enhancements.js + enhancements.css")
    add("status_errors_empty", not (status.get("errors") or []), status.get("errors") or [])
    firefox = status.get("chatgpt_firefox") or {}
    firefox_runtime_active = firefox.get("active") is True or firefox.get("state") == "active"
    add(
        "firefox_runtime_active",
        firefox_runtime_active or legacy_firefox_disabled,
        {
            "runtime": firefox,
            "legacy_violentmonkey_only": legacy_firefox_disabled,
        },
    )
    projects = targets_payload.get("projects")
    add("runner_targets_available", isinstance(projects, dict), {
        "project_count": len(projects) if isinstance(projects, dict) else None,
        "max_workers": targets_payload.get("max_workers"),
    })
    add("mapping_available", mapping.get("available") is True, mapping)
    try:
        api_worker_limit = int(targets_payload.get("max_workers"))
    except (TypeError, ValueError):
        api_worker_limit = None
    try:
        status_worker_limit = int((status.get("dynamic_workers") or {}).get("count"))
    except (TypeError, ValueError):
        status_worker_limit = None
    sqlite_worker_limit = mapping.get("dynamic_worker_limit")
    allocation = targets_payload.get("global_allocation") or {}
    allocated_workers = allocation.get("workers")
    allocated_workers = allocated_workers if isinstance(allocated_workers, list) else None
    limits_match = (
        api_worker_limit is not None
        and status_worker_limit == api_worker_limit
        and sqlite_worker_limit == api_worker_limit
    )
    add(
        "dynamic_worker_limit_consistent",
        limits_match,
        {
            "sqlite": sqlite_worker_limit,
            "status": status_worker_limit,
            "runner_targets": api_worker_limit,
            "allocated_workers": len(allocated_workers) if allocated_workers is not None else None,
        },
    )
    allocation_bounded = allocated_workers is not None and api_worker_limit is not None
    if allocation_bounded:
        seen_slots = []
        for worker in allocated_workers:
            try:
                seen_slots.append(int(worker.get("global_worker_slot")))
            except (TypeError, ValueError, AttributeError):
                allocation_bounded = False
                break
        allocation_bounded = (
            allocation_bounded
            and len(allocated_workers) <= api_worker_limit
            and len(set(seen_slots)) == len(seen_slots)
            and all(1 <= slot <= api_worker_limit for slot in seen_slots)
        )
    add(
        "dynamic_worker_allocation_bounded",
        allocation_bounded,
        {
            "limit": api_worker_limit,
            "slots": [
                worker.get("global_worker_slot")
                for worker in allocated_workers
            ] if allocated_workers is not None else None,
        },
    )
    if expected_mapping_sha:
        add(
            "mapping_unchanged",
            mapping.get("sha256") == expected_mapping_sha,
            {"expected": expected_mapping_sha, "actual": mapping.get("sha256")},
        )
    add(
        "firefox_source_runtime_match",
        source_runtime_match or legacy_firefox_disabled,
        {
            "match": source_runtime_match,
            "legacy_violentmonkey_only": legacy_firefox_disabled,
        },
    )

    runners = status.get("chatgpt_runners") or {}
    if require_worker_read_model:
        bad = []
        for project_id, runner in runners.items():
            if not isinstance(runner.get("workers"), list):
                bad.append(project_id + ":workers")
            if "desired_worker_count" not in runner:
                bad.append(project_id + ":desired")
            if "active_worker_count" not in runner:
                bad.append(project_id + ":active")
        add("worker_read_model", not bad and bool(runners), bad or "present")

    if require_incidents:
        incidents = status.get("incidents")
        ok = isinstance(incidents, dict) and isinstance(incidents.get("items"), list)
        add("incident_center", ok, incidents if ok else "missing/invalid")

    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "mapping": mapping,
    }


def live_canary(
    *,
    root: Path = ROOT,
    db_path: Path = DB,
    base_url: str = BASE_URL,
    runtime_extension: Path = RUNTIME_EXTENSION,
    expected_mapping_sha: str | None = None,
    require_worker_read_model: bool = False,
    require_incidents: bool = False,
) -> dict:
    errors = []
    try:
        status = http_json_ready(base_url + "/api/status")
    except Exception as exc:
        status = {"errors": [f"status unavailable: {exc}"]}
        errors.append(str(exc))
    try:
        targets = http_json(base_url + "/api/runner-targets")
    except Exception as exc:
        targets = {}
        errors.append(str(exc))

    parity = firefox_runtime_parity(root, runtime_extension)
    source_runtime_match = parity["ok"]
    legacy_firefox_disabled = legacy_firefox_intentionally_disabled()
    static_ok = all(http_ok(base_url + path) for path in (
        "/app.js", "/enhancements.js", "/enhancements.css"
    ))
    result = evaluate(
        status,
        targets,
        mapping_fingerprint(db_path),
        services={
            "zcloud": service_active("zennay-cloud.service"),
            "firefox": service_active("chatgpt-firefox.service", user=True),
        },
        static_assets_ok=static_ok,
        source_runtime_match=source_runtime_match,
        legacy_firefox_disabled=legacy_firefox_disabled,
        expected_mapping_sha=expected_mapping_sha,
        require_worker_read_model=require_worker_read_model,
        require_incidents=require_incidents,
    )
    result["firefox_runtime_parity"] = parity
    result["legacy_firefox_disabled"] = legacy_firefox_disabled
    if errors:
        result["transport_errors"] = errors
        result["ok"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate zCloud after a deploy")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--runtime-extension", type=Path, default=RUNTIME_EXTENSION)
    parser.add_argument("--expect-mapping-sha")
    parser.add_argument("--require-worker-read-model", action="store_true")
    parser.add_argument("--require-incidents", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = live_canary(
        root=args.root.resolve(),
        db_path=args.db.resolve(),
        base_url=args.base_url.rstrip("/"),
        runtime_extension=args.runtime_extension.resolve(),
        expected_mapping_sha=args.expect_mapping_sha,
        require_worker_read_model=args.require_worker_read_model,
        require_incidents=args.require_incidents,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(("OK" if check["ok"] else "FAIL").ljust(5), check["name"], check["detail"])
        print("POSTDEPLOY_GREEN" if result["ok"] else "POSTDEPLOY_BLOCKED")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
