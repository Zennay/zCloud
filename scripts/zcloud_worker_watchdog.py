    global_action = None
    if selected_keys and len(global_recovery_candidates) == len(selected_keys):
        global_state = state.setdefault("global", {})
        if seconds_since(global_state.get("last_firefox_restart_at"), now) >= GLOBAL_RECOVERY_COOLDOWN_SECONDS:
            if dry_run:
                global_action = {"action": "restart_firefox", "ok": True, "dry_run": True}
            else:
                status_code, payload = api_call(
                    base_url,
                    "POST",
                    "/api/runner-control",
                    {"project_id": "", "action": "restart_firefox"},
                )
                accepted = status_code < 300 and bool(payload.get("ok"))
                global_action = {
                    "action": "restart_firefox",
                    "ok": accepted,
                    "http_status": status_code,
                    "response": payload,
                }
                if accepted:
                    global_state["last_firefox_restart_at"] = now.isoformat()
    global_action = None
    if selected_keys and len(global_recovery_candidates) == len(selected_keys):
        # All workers being stalled must not become a browser-wide kill signal.
        # Only per-worker in-tab recovery is allowed here.
        global_action = {
            "action": "restart_firefox_suppressed",
            "ok": True,
            "reason": "stalled-workers-preserve-existing-tabs",
        }

    state["updated_at"] = now.isoformat()
    state["version"] = 1
    if not dry_run:
        save_state(state_path, state)

    return {
        "ok": True,
        "checked_at": now.isoformat(),
        "allocated_workers": sorted(selected_keys),
        "worker_count": len(selected_keys),
        "workers": reports,
        "global_action": global_action,
        "firefox": firefox,
        "memory_guard": memory_guard,
        "stale_commands_cleared": stale_commands_cleared,
        "thresholds": {
            "push_after_seconds": PUSH_AFTER_SECONDS,
            "restart_after_seconds": RESTART_AFTER_SECONDS,
            "offline_restart_after_seconds": OFFLINE_RESTART_AFTER_SECONDS,
            "quiet_grace_seconds": QUIET_GRACE_SECONDS,
            "generation_protect_seconds": GENERATION_PROTECT_SECONDS,
            "prompt_stale_refresh_seconds": PROMPT_STALE_REFRESH_SECONDS,
            "global_recovery_after_seconds": GLOBAL_RECOVERY_AFTER_SECONDS,
            "firefox_runtime_restart_cooldown_seconds": FIREFOX_RUNTIME_RESTART_COOLDOWN_SECONDS,
            "stale_pending_command_seconds": STALE_PENDING_COMMAND_SECONDS,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="zCloud dynamic-worker material-progress watchdog")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear-stale-pending-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.clear_stale_pending_only:
        cleared = clear_stale_pending_commands(
            args.db,
            project_id=None,
            now=utc_now(),
            min_age_seconds=STALE_PENDING_COMMAND_SECONDS,
        )
        result = {
            "ok": True,
            "cleared_command_ids": cleared,
            "threshold_seconds": STALE_PENDING_COMMAND_SECONDS,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, sort_keys=True))
        return 0

    result = run_once(
        db_path=args.db,
        state_path=args.state_file,
        base_url=args.base_url,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
