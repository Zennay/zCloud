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

## Compare progress across owner-controlled recovery windows

Save a JSON snapshot only in a **private operator-controlled** location (standard output is never automatically stored), and use `--previous-report /private/path/to/previous.json` on a later observation with either text, JSON or HTML format. Output then separates **new**, **resolved** and **still-present** finding codes, allowing operators to focus on genuinely changed blockers instead of repeatedly triaging the same issue. The older file is read without mutation; filenames, notes and free-text from older snapshots are not surfaced. Oversized, malformed, symlinked or untrusted snapshot data is refused with a safe fixed error. This comparison is not an independent CI, main provenance or release authorization mechanism.

Severity changes such as a warning becoming a hard blocker are reported separately rather than falsely labelled unchanged. An earlier report timestamp must be well-formed and no later than the current observation; future-dated or naive history is rejected.

## Bounded disk triage (optional)

Add `--disk-hotspots` to include disk sizes for **five specifically named categories** only: checkout, zCloud runner installation, user cache, Gradle cache and RaiseAI build directory alongside the checkout's parent. All `du` operations are read-only, use argv rather than shell expansion, and have a three-second deadline each. Missing or timed-out categories are shown as **incomplete**, never as zero. Default runs incur **no recursive disk scan**. The operator can inspect candidates before any separately authorized cleanup; the preflight cannot remove caches, log files, runner builds or other workers' files.


## Inspect external archive availability without moving data

Use `--archive-readiness` to inspect the configured `/home/ubuntu/VPS-Archief` mount as part of the same read-only report. This checks the *exact* mountpoint in `/proc/self/mountinfo`; an unmounted directory is not mistaken for an archive. It also detects the existing rclone process when it explicitly runs with `--read-only`, without printing arguments, secrets or environment variables.

```sh
python3 scripts/zcloud_vps_preflight.py --archive-readiness --format html > private-preflight.html
```

The resulting report distinguishes `not_mounted`, `mount_declares_read_only`, `rclone_client_read_only`, and `mounted_write_unverified`. **Even a mounted read/write FUSE filesystem is not proof of permission to copy or purge data.** Archive readiness is a warning and an owner-directed next step, never an authorization to transfer, mount differently, overwrite, delete source files or bypass archive encryption/checksums. No write probes are attempted. On 20 October the connected VPS was observed running an explicitly `--read-only` rclone archive mount while local disk usage was around 91%; this flag is not a safe migration destination. Confirm separately that a legitimate encrypted writable destination and permissions exist, verify remote hashes, and authorize source removal only after reliable independent proof.

Git subprocesses in the preflight now drop *all* inherited `GIT_*` variables (not just three common index/worktree overrides), while using `GIT_OPTIONAL_LOCKS=0`, so inherited Git namespace, configuration and alternate-object overrides cannot redirect its checkout inspection. This is local hardening, not an assertion that the production checkout has been reconciled.

The preflight is deliberately **not** a deployment gate, an auto-cleanup script or a full runner-registration probe: the live listener process can exist without an assigned successful GitHub job. Exact-head protected-main CI, independent review and the serialized release owner remain mandatory.


## Protect ongoing worker generations during a release decision

The optional `--worker-safety` mode reads `history.db` with a **new SQLite `mode=ro` connection** and `PRAGMA query_only`. It never calls the mutating worker control-plane HTTP GET routes. This mode is strictly bounded to aggregate counters and recent (at most 301) browser events. It never extracts prompt text, conversation URLs, chat content, private claim owners, worker IDs or queue payloads.

```bash
python3 scripts/zcloud_vps_preflight.py \
  --worker-safety --archive-readiness --expected-main-sha "$VERIFIED_MAIN_SHA" \
  --format html > private-zcloud-release-preflight.html
```

The snapshot reports:
- `queued_claims`: number of currently **claimed** queue rows, conservatively counting stale claims rather than assuming it is safe to interrupt them. This is not the total number of runnable workers.
- `durable_claims`: outstanding entries in the separate `task_claims` table, again counted conservatively.
- `fresh_generating_slots`: distinct project+slot keys whose latest event among a bounded read-only snapshot is no older than 300 seconds and whose browser-declared generating flag is true.
- `events_sample_complete`: whether the bounded sample covers the recent observation window; when the source is unavailable, malformed, in the future, or sampling is incomplete, the report **blocks instead of guessing idle**.

When live claims or recently reported generation exist, the preflight returns `ACTIVE_WORK_REQUIRES_DRAIN` and points the operator to the existing serialized worker/release owner. This does not touch the queue or request a drain. The initial direct connected zSSH inspection on 20 October observed `queued_claims=5`, `fresh_generating_slots=5`, `safe_idle_verified=false`. These are browser/queue observations; they do not prove generated text, agent billing, actual tab visibility or whether individual threads should continue.

**No observation can grant safe-idle:** even zero claims and zero generating events returns `no_active_evidence_unverified` and `safe_idle_verified=false`. The original production safe-idle gate, claim ownership, exact-main provenance, review, CI, device/security gates and serialized human release authorization remain mandatory. An operator must not kill workers, reset queue claims, restart runners, unmount archives, or promote source using this read-only report.

Current production `history.db` can be large and active; each invocation uses a bounded read-only transaction, a short SQLite busy timeout, and only recent event identities. Do not run it in a high-frequency polling loop or infer the absence of activity when SQLite access fails.
