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
   A killed process may leave the already verified approved version installed
   but cannot leave a half-copied background file. These are operator-denied
   installs, not permission to overwrite active-owner work. The new shared
   read-only `scripts/zsentinel_addon_idle_gate.py` checks the authenticated
   local runner state twice across a 12-second window **before staging the
   live install**, repeats the check immediately before the atomic rename,
   and rechecks again immediately before reload. Busy/draining/pending,
   malformed, changing or unreadable workers deny installation and/or defer
   reload; unknown is never treated as idle. The old flow could already
   replace live addon bytes before doing its first idle check. Worker
   sessions and tabs are not forcibly restarted.
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
