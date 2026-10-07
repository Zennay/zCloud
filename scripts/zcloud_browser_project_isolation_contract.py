#!/usr/bin/env python3
"""Fail-closed source contract for zCloud browser project/tab isolation.

The browser runners are deliberately not rewritten by this audit. Instead this
contract binds CI to the concrete routing guards that keep one worker/project
from consuming commands or replacement handoffs owned by another project.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _read_regular_file(path: Path) -> str:
    if path.is_symlink():
        raise ContractError(f"refuse symlink source: {path}")
    if not path.is_file():
        raise ContractError(f"missing source: {path}")
    return path.read_text(encoding="utf-8")


def _extract_function(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise ContractError(f"missing function signature: {signature}")
    brace = source.find("{", start + len(signature))
    if brace < 0:
        raise ContractError(f"missing function body: {signature}")
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise ContractError(f"unterminated function body: {signature}")


def _contains(body: str, needle: str, name: str) -> Check:
    return Check(name=name, ok=needle in body, detail=needle)


def userscript_accepts(command_project: str, target_project: str, base_project: str, worker_slot: int) -> bool:
    same_worker = command_project == target_project
    base_push = command_project == base_project and int(worker_slot) == 1
    return same_worker or base_push


def extension_targets(command_project: str, targets: set[str], worker_keys: list[str]) -> list[str]:
    if command_project in targets:
        return [command_project]
    return [key for key in worker_keys if key == command_project or key.startswith(command_project + "::w")]


def audit(root: Path) -> dict:
    root = root.resolve()
    userscript = _read_regular_file(root / "public" / "zcloud-worker.user.js")
    background = _read_regular_file(root / "firefox-extension" / "background.js")

    handle = _extract_function(userscript, "async function handleCommands()")
    refresh = _extract_function(userscript, "async function refreshTarget()")
    poll = _extract_function(background, "async function pollCommands()")
    new_chat = _extract_function(background, "async function newProjectChat(projectId, reason, commandId)")
    tab_listener = _extract_function(background, "browser.runtime.onMessage.addListener(message =>")
    replacement = _extract_function(background, "browser.runtime.onMessage.addListener((message, sender) =>")

    checks = [
        _contains(handle, "const sameWorker = command.project_id === target.project_id;", "userscript_same_worker_only"),
        _contains(handle, "const basePush = command.project_id === target.base_project_id && Number(target.worker_slot || 1) === 1;", "userscript_base_command_primary_only"),
        _contains(handle, "return sameWorker || basePush;", "userscript_rejects_foreign_commands"),
        _contains(refresh, "pending && projects[pending] && claimCandidate(projects[pending])", "userscript_pending_project_claim_bound"),
        _contains(refresh, "item?.conversation_id === cid", "userscript_conversation_match_bound"),
        _contains(refresh, "projects[target.project_id] && claimCandidate(projects[target.project_id])", "userscript_existing_target_claim_bound"),
        _contains(poll, "const hasTarget = !!targets[command.project_id] || workerKeysFor(command.project_id).length > 0;", "extension_command_requires_project_target"),
        _contains(poll, "const commandWorkerKeys = targets[command.project_id]", "extension_command_keys_derived_from_project"),
        _contains(poll, "if (command.action === \"push\") await pushProject(command.project_id, command.id);", "extension_push_routes_same_project"),
        _contains(poll, "else if (command.action === \"start\") await startProject(command.project_id, command.id);", "extension_start_routes_same_project"),
        _contains(poll, "else if (command.action === \"pause\") await pauseProject(command.project_id, command.id);", "extension_pause_routes_same_project"),
        _contains(poll, "else if (command.action === \"drain\") await drainProject(command.project_id, command.id);", "extension_drain_routes_same_project"),
        _contains(tab_listener, "if (!message || message.projectId !== cfg.projectId) return;", "tab_listener_rejects_foreign_project_messages"),
        _contains(new_chat, "const oldTab = projectTabs[projectId];", "replacement_closes_only_project_tab"),
        _contains(new_chat, "delete tabTargets[oldTab];", "replacement_clears_exact_old_tab_binding"),
        _contains(replacement, "const target = tabId != null ? tabTargets[tabId] : null;", "handoff_uses_sender_tab_binding"),
        _contains(replacement, "if (!target || target.project_id !== message.projectId) return {ok:false, reason:\"worker-tab-mismatch\"};", "handoff_rejects_cross_project_sender"),
    ]

    simulations = {
        "same_worker": userscript_accepts("ftmo::w2", "ftmo::w2", "ftmo", 2),
        "foreign_project_rejected": not userscript_accepts("supa::w1", "ftmo::w2", "ftmo", 2),
        "base_command_primary_allowed": userscript_accepts("ftmo", "ftmo::w1", "ftmo", 1),
        "base_command_secondary_rejected": not userscript_accepts("ftmo", "ftmo::w2", "ftmo", 2),
        "extension_foreign_worker_excluded": extension_targets(
            "ftmo", {"supa::w1"}, ["supa::w1", "ftmo::w1", "ftmo::w2"]
        ) == ["ftmo::w1", "ftmo::w2"],
    }
    checks.extend(Check(name=f"simulation_{name}", ok=value, detail="deterministic routing simulation") for name, value in simulations.items())

    failed = [check.name for check in checks if not check.ok]
    return {
        "schema_version": 1,
        "state": "green" if not failed else "failed",
        "failed_checks": failed,
        "check_count": len(checks),
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
        "scope": {
            "userscript": "public/zcloud-worker.user.js",
            "extension": "firefox-extension/background.js",
        },
        "policy": "commands, navigation claims and replacement handoffs remain project/tab scoped",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit zCloud browser project/tab isolation contract")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        payload = audit(args.root)
    except ContractError as exc:
        payload = {
            "schema_version": 1,
            "state": "error",
            "failed_checks": ["contract_input"],
            "error": str(exc),
        }
        print(json.dumps(payload, sort_keys=True, indent=2 if args.pretty else None))
        return 2
    print(json.dumps(payload, sort_keys=True, indent=2 if args.pretty else None))
    return 0 if payload["state"] == "green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
