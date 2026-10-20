# zCloud queue watchdog — read-only operator handoff

This documents existing zCloud product PR #1551. It does **not** authorize a runner restart, cancellation, workflow rerun or production deployment.

## What changes

The existing `.github/workflows/zcloud-self-hosted-queue-watchdog.yml` now uses only `actions: read` and `contents: read` GitHub token permissions. It observes queued self-hosted Actions jobs without cancelling duplicates, setting up a second worker, draining a queue or invoking `sudo systemctl restart`. The legacy `recover` job was removed entirely; its previous `if: false` safeguard is no longer the only protection.

The five-minute schedule and `workflow_run` trigger are absent. The remaining on-demand `workflow_dispatch` is restricted to the canonical repository's `main` branch and explicit owner actor; a push to `main` triggers observation only when this workflow file changes. The observer requires the dedicated `[self-hosted, zcloud, vps]` runner and **cannot restart an offline listener by itself**. No GitHub-hosted compute is requested. Existing noninterrupting concurrency is retained.

## What the operator sees

A bounded read-only GitHub Actions step summary reports how many queued runs were inspected, observed queued self-hosted jobs, jobs older than 180 seconds and the lower bound of uninspected queued runs. At most 16 queued runs and up to 100 jobs per run are read; hitting those source limits makes `coverage_complete=false`. A partial response or zero jobs **never proves** there is no outstanding work. `stale_detected` and the JSON evidence output are **advisory observations only**, with `cancel_performed=false`, `restart_performed=false` and `recovery_authorized=false`.

Only bounded counts, run/job IDs and ages participate in the observer's local state; it does not export free-text job paths/names, prompts or tokens. Provider denials fail the workflow rather than silently reporting zero queued jobs. No automatic polling, background retry or change to the VPS database is added.

## Release gate

The unrelated required `zcloud-regression-smoke.yml` was restored byte-for-byte to its current protected-`main` content. Required CI must not be suppressed or marked successful by an `if: false` admission branch, and no manually requested QA/test suites are part of this PR.

Source changes are **not yet on `main` or installed on the VPS**. Existing exact-head required CI, independent approval, branch-conflict reconciliation, and authorized serialized production release remain required. The current resource-governor/runner work stays with its active owner. If the self-hosted listener is down or queues are exhausted, recovery belongs to the existing authorized resource and release owner, not this observer.
