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
3. Only the live job acquires the non-cancelling production lock. Its runtime
   checks verify exact checkout, remote main and permanent VPS identity.
4. The worker-tab workflow now checks remote main **again** before copying
   the browser background file and before attempting an in-place addon reload.
   Existing idle checks decide whether activation is safe; neither worker
   sessions nor tabs are forcibly restarted.
5. A changed main, unknown runner, missing permission, non-idle worker or
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
