# Read-only zCloud VPS deploy preflight

Run from the connected, already-authorized zSSH channel:

```bash
python3 scripts/zcloud_vps_preflight.py --json --expected-main-sha "$VERIFIED_MAIN_SHA"
```

Set `VERIFIED_MAIN_SHA` to an independently obtained *current* 40-character GitHub main SHA before invoking the command; never infer it from the dirty VPS checkout. This optional binding reports a blocked local-vs-main drift. Without the SHA the output is partial even if all local checks pass, never release authorized. Git identity comes from the checkout with inherited Git override environment variables excluded.

This command inspects local Git worktree identity and tracked/untracked changes, the dedicated zCloud self-hosted Runner.Listener, its unit state where readable, memory/swap and free disk. It never executes Git writes, restarts a service, calls an API, contacts GitHub, accesses SQLite, modifies workers or authorizes a release. No raw process arguments, file contents or credentials are returned. Exit codes: 2 = blocked, 1 = partial visibility, 0 = local checks clear (not permission to deploy).

**Critical limitation:** a clean local report is not evidence of protected-main approval, exact-head CI, active GitHub runner registration, a healthy web UI, owner leases or production status. Existing #1326 runner recovery, #1447 writer serialization and #580/#1089 release ownership are still required. Do not act on a failed/inactive runner merely from this report. Staged isolated execution in a scratch location can be used to inspect the live host without changing its dirty production checkout.

Use this as a short preflight for a human release owner, not an auto-healing agent.

## Owner-bound recovery priorities

The JSON report now exposes ordered `recovery_priorities` with codes, accountable owner, reason and safe next action. Human output prints the same order. Capacity is handled first (especially a full swap or critically low disk), followed by dedicated runner ownership, dirty checkout reconciliation and exact-source provenance. These are **suggestions only**; no programmatic recovery, cleaning, `git pull`, restart, database mutation or runner reassignment takes place. The final item always reminds the operator that successful local inspection does not grant deploy permission.

This workflow is intentionally not wired into existing production GitHub Actions jobs, owned by #580/#1089 and #1447. It does not depend on a GitHub token or make any network request.

## Optional offline HTML operator view

```sh
python3 scripts/zcloud_vps_preflight.py --expected-main-sha "$VERIFIED_MAIN_SHA" --format html > preflight-report.html
```

This optional output is a **standalone HTML snapshot**, responsive on a phone or desktop and printable. It embeds no JavaScript, external dependencies, credentials or detailed process arguments. All derived text is HTML-escaped. Display contains release blockers, Git/ref context, resource conditions and human owner-bound recovery steps. By default only stdout is written; redirection is an explicit **operator** decision and should target a private location, never a public web root. The process still returns a nonzero code on blocked or incomplete inspections even if an HTML page was emitted.
