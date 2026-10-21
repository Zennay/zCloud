# zSentinel · serialized manual VPS writers (#1447)

Three existing, manually authorized VPS mutation jobs now share the canonical
**job-level** `zcloud-production-deploy` lock, with
`cancel-in-progress: false`:

| Workflow | Live job | Mutation |
| --- | --- | --- |
| `.github/workflows/zcloud-worker-tab-guard.yml` | `install` | Install background code and conditionally activate addon |
| `.github/workflows/zcloud-no-browser-churn-hotfix.yml` | `install` | Install guarded watchdog script |
| `.github/workflows/zcloud-emergency-addon-hot-reload.yml` | `activate` | Reload existing addon only after a safe idle gate |

The pull-request validation jobs remain outside the shared production lock.
This avoids deadlocking unrelated source validation and prevents a waiting
production writer from cancelling an in-flight deployment.

## Manual operational flow

1. A human owner approves a particular **current main** 40-character SHA.
   This change does not authorize or invoke a manual workflow.
2. The live job still requires `workflow_dispatch`, `refs/heads/main`,
   `Zennay` as actor and triggering actor, the first attempt only, and the
   matching explicit `apply` or addon authorization input.
3. Only the live job acquires the non-cancelling production lock. Its first
   executable shell step runs the canonical permanent-VPS identity guard,
   immediately after a credentials-disabled checkout at the workflow event SHA.
   Later owner gates still verify exact checkout, remote main and VPS identity.
4. The worker-tab workflow checks remote main **again** before the live
   add-on write and before the in-place reload. It refuses a runtime checkout
   whose HEAD differs from approved main, dirty/untracked runtime background
   changes, or runtime bytes that do not exactly equal the approved Actions
   source. The **tracked VPS source file is never overwritten** by the install.
   Symlinked add-on paths and a missing installed background also deny apply.
   For the installed copy only, the job stages the approved bytes in the same
   directory, saves the previous file there, and atomically renames the staged
   file into place; it verifies the installed result, restores the previous
   version on failure, and retains its recovery copy if restoration is denied.
   After the final slow idle observation it checks the live file against the
   previously saved bytes, verifies it has not turned into a symlink and
   reconfirms source equality. Concurrent owner changes abort before the
   atomic replacement rather than being silently overwritten.
   A killed process may leave the already verified approved version installed
   but cannot leave a half-copied background file. These are operator-denied
   installs, not permission to overwrite active-owner work. The new shared
   read-only `scripts/zsentinel_addon_idle_gate.py` checks the loopback-only
   local runner state twice across a 12-second window **before staging the
   live install**, repeats the check immediately before the atomic rename,
   and rechecks again immediately before reload. Busy/draining/pending,
   malformed, changing or unreadable workers deny installation and/or defer
   reload; unknown is never treated as idle. The observer also rejects
   stale/offline worker states, event timestamps older than 90 seconds,
   future-dated timestamps and missing/non-boolean activity fields. Its
   decision is a bounded point-in-time observation, **not** a lock or
   replacement for actual owner approval. The old flow could already replace
   live addon bytes before doing its first idle check. Worker sessions and
   tabs are not forcibly restarted. Denied admission has a fixed non-sensitive operator
   category (`BUSY`, `CHANGED`, `SOURCE_UNAVAILABLE`,
   `SOURCE_UNVERIFIABLE` or `MALFORMED_SOURCE`), without printing worker
   identities, telemetry payloads or local paths. On 21 October a bounded
   read-only observation of the existing local `/api/runner-live` route
   timed out after seven seconds: no live-idle proof exists for that instant,
   and source health must be resolved separately before an approved install.
5. All three VPS checkout steps rely on the canonical GitHub workflow-event
   SHA selected by the pinned `actions/checkout`. Explicit `ref` expression
   overrides in these privileged jobs were removed because they were rejected
   by the mandatory permanent-runner source gate. The separate manual
   owner/triggering-actor/first-attempt/exact-main checks remain required.
6. The manual watchdog workflow also protects the **shared runtime
   checkout**. If it exists, exact approved HEAD, a non-symlink script path,
   a clean tracked/untracked watchdog path and any existing source bytes must
   all agree before installing the separately authorized standalone watchdog.
   It does not update the shared runtime script: an owner-modified/untracked
   file must be resolved by that owner first. Read-only zSSH observation on
   21 October found an untracked watchdog script in the VPS runtime checkout;
   this is a **real non-overwrite case**, not permission to reset it. The
   standalone promotion now completes runtime-source admission *before* any
   backup directory/file creation, denies symlinked root targets or colliding
   backup names, and stores any previous standalone executable as an owner-only
   mode-0600 backup. Backups do not appear in normal GitHub Actions logs.
   Standalone watchdog promotion no longer streams approved source bytes
   directly over the live executable path. It stages a mode-0755,
   root-owned executable **in /usr/local/sbin** and compares bytes first;
   only then does a same-filesystem rename publish the new version. This
   avoids exposing truncated/partially copied watchdog files during an
   update. A second exact-main check and existing-live-byte comparison
   immediately precede promotion. A post-promotion failure attempts a
   guarded restore from the saved private backup (or removes a newly
   introduced file), but refuses to roll back over a different owner's
   intervening edits. On rollback failure the owner's private backup stays
   available for supervised restoration. This procedure never stops or
   restarts a watchdog, browser, runner or worker process.
7. The emergency reload workflow rechecks actual installed addon bytes,
   trusted/non-symlink paths and its tab-open/close restrictions **after**
   the final current-main and worker-idle checks, immediately before invoking
   the addon-only reload. The earlier preflight alone is never sufficient.
8. A changed main, unknown runner, missing permission, non-idle worker or
   failed step leaves activation denied or deferred according to the existing
   fail-closed behavior.

## Delivery boundaries

This is a source change on a feature branch. No workflow was dispatched, no
new QA suite was created or run, no runner / Firefox / service was restarted,
and no live database, queue, claims or production code was modified. Existing
required CI, independent review, protected-main admission and serialized
human-authorized deployment remain mandatory. Neither a successful commit nor
a merge proves that the VPS runner is healthy or that a live installation
occurred.

Related infrastructure blockers are tracked independently:
- Runner listener OOM / unavailable: https://github.com/Zennay/zCloud/issues/1326
- GitHub-hosted Actions billing/admission failure: https://github.com/Zennay/zCloud/issues/1523
- Production writer lock / release admission: https://github.com/Zennay/zCloud/issues/1447

The old unmerged source branches for individual workflows were inspected as
ownership signals; none had an open PR at the time of this change.


## Operator delivery receipts (GitHub Actions)

Each of the three manual live jobs now appends a source-bound observation
to the **GitHub Actions job summary** via
`scripts/zsentinel_release_receipt.py`. The formatter accepts only fixed
component/outcome combinations and a canonical owner-dispatched exact-main
GitHub run context. It emits the exact 40-character workflow SHA, run link,
UTC observation time, outcome and next operator action. It does not read
secrets, runner identities, raw responses or backup paths and does not provide
additional deployment authority.

| Manual operation | GitHub summary outcome | What it actually means |
| --- | --- | --- |
| Worker addon install, after source bytes are compared | `INSTALLED_ON_DISK` | Installed file verified; **browser activation not confirmed** |
| Worker addon: no debugger, busy/unverified idle, or failed reload command | `RELOAD_DEFERRED` | Disk install may be complete; **not actively reloaded** |
| Worker addon: in-place reload command returned success | `RELOAD_COMMAND_SUCCEEDED` | Reload command worked; **not independent end-to-end live proof** |
| Standalone watchdog install with final byte comparison | `INSTALLED_ON_DISK` | Standalone executable updated; **no live recovery proof** |
| Owner-approved emergency addon reload preserving Firefox PID | `RELOAD_COMMAND_SUCCEEDED` | Requested reload succeeded; **independent behaviour remains unverified** |

A green GitHub job with a deferred action is **not** equivalent to a live
upgrade. Where observation is denied by a missing debugger, worker activity or
a failed reload, the summary explicitly recommends a later owner-approved
activation window rather than silently declaring completion.

If an on-disk installation **fails after the atomic rename**, its EXIT cleanup
preserves the original failing job status and emits an additional operator
observation where possible:

| Failed-install observation | Meaning |
| --- | --- |
| `PREVIOUS_VERSION_RESTORED` | Previously installed addon/watchdog bytes were restored after failure |
| `NEW_INSTALL_REMOVED` | First-time standalone watchdog install was removed; nothing existed before |
| `CONCURRENT_OWNER_CHANGE_PRESERVED` | A newer installed version was observed and deliberately **not** overwritten by rollback |
| `ROLLBACK_FAILED` | Automatic restoration failed; supervised recovery and the retained backup may be required |

These outcomes do not make a failed job green. Receipt emission is
best-effort during EXIT cleanup, cannot mask its original failure, and
never grants mutation authority. The addon rollback refuses to replace a
symlink or owner-modified add-on after its own promotion. The watchdog
rollback stages and compares the private backup before restoring via an
atomic rename; if restoration fails, the private backup is retained.
A job that fails **before** installing anything remains a failed job,
not a successful release receipt.

The worker-add-on install and activation are separate shell steps. The
activation step explicitly re-resolves its installed add-on directory; using
the shell-local `live` variable from the preceding install step used to fail
under `set -u` before any reload, despite a successful on-disk copy.

**Important:** These receipts describe what a GitHub manual workflow has
observed *after* authorized execution. They are not proof of merged PR code,
a recovered runner, or independent live verification. No workflows have been
manually dispatched as part of this source change.
