# Read-only zCloud VPS deploy preflight

Run from the connected, already-authorized zSSH channel:

```bash
python3 scripts/zcloud_vps_preflight.py --json
```

This command inspects local Git worktree identity and tracked/untracked changes, the dedicated zCloud self-hosted Runner.Listener, its unit state where readable, memory/swap and free disk. It never executes Git writes, restarts a service, calls an API, contacts GitHub, accesses SQLite, modifies workers or authorizes a release. No raw process arguments, file contents or credentials are returned. Exit codes: 2 = blocked, 1 = partial visibility, 0 = local checks clear (not permission to deploy).

**Critical limitation:** a clean local report is not evidence of protected-main approval, exact-head CI, active GitHub runner registration, a healthy web UI, owner leases or production status. Existing #1326 runner recovery, #1447 writer serialization and #580/#1089 release ownership are still required. Do not act on a failed/inactive runner merely from this report. Staged isolated execution in a scratch location can be used to inspect the live host without changing its dirty production checkout.

Use this as a short preflight for a human release owner, not an auto-healing agent.
