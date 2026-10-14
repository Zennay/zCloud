# zCloud deploy-ops — read-only production gate receipt

Date: 2026-10-14. Scope: independent deploy-ops observation, not the serialized deployment owner.

## Why it exists

A green hosted Python/Node run is **not** proof that the VPS application is deployed
or that workers are generating. We observed `main@e5a63e3efb508d8b0a42c148262f9515b5753bed`
with a real GitHub `zcloud/vps-production=failure` receipt ("VPS production
promotion failed"). Separate historical failures include SQLite write denial
and safe-idle worker drain timeouts. Runner route and deployment outcome must
not be treated as the same condition.

## New capability

`scripts/zcloud_deployops_readonly_gate_20261014.py` compares two independent
main-branch reads with the exact commit status SHA, then fetches the most recent
15 deploy-workflow runs and their bounded GitHub jobs lists. It reports only
a fixed, sanitized state and numeric identifiers. It accepts no arbitrary
API URLs, actions that write, workflow dispatch, cancellation, runner restart,
SQLite access, filesystem promotion, or browser interactions.

A deployed main status of `success` is **STATUS_GREEN_UNVERIFIED**, rather than
live runtime proof. `DEPLOY_JOB_QUEUED_UNASSIGNED` does not assert the VPS
listener is down: labels, repository entitlement, concurrency, runner groups,
or a skipped job may be involved. `OBSERVED_MAIN_DEPLOY_FAILURE` means a matching
main deployment run has failed; it is not a permission to retry it.

`DENY_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE` suppresses counts and links when
anything needed is stale/missing, unbounded, malformed, cross-run, or when main
moves during observation. Every valid and denied report carries
`mutation_authorized=false`, `release_authorized=false`,
`production_verified=false` and `runner_outage_verified=false`.

## How to check it

- Automated isolated hosted test: [workflow](https://github.com/Zennay/zCloud/actions/workflows/zcloud-deployops-readonly-gate-20261014.yml).
- Read-only operator invocation: `GITHUB_TOKEN=<read-only-token> python3 scripts/zcloud_deployops_readonly_gate_20261014.py --live`.
- Offline negative-case replay: `python3 -m unittest -v tests.test_zcloud_deployops_readonly_gate_20261014`.

The hosted workflow is restricted to its own branch; it performs an immutable
checkout, runs adversarial tests, full Python regression, a shell/Node smoke, then makes GET-only requests with
`contents:read`, `actions:read` and `statuses:read`. The live observer's
diagnosis is **not** an acceptance or production-release gate, even if source
tests are green. Each hosted execution publishes a sanitized human-readable GitHub
job summary and a seven-day JSON evidence artifact; neither is recovery authority.

## Integration owner boundaries

Existing exclusive owners are #580/PWQ-41 + #1089 for serialized deployment,
#1090 for safe-idle retries, #1317 for listener recovery, #1338 for live SQLite
write access, #1091 for the privileged queue watchdog and #1135 for PR-triggered
recovery hardening. Do not merge, enable scheduled production recovery,
restart a worker, cancel runs or remediate SQLite from this observer.

Hand off a terminal exact-head source receipt plus separately verified live
production proof to the proper owner. If the legacy PR-triggered recovery
path of #1135 has not been proven nonmutating on pull requests, **do not open a
PR solely to trigger tests**; keep the code on its branch until that release
security condition has been independently reviewed.

## What remains outside this feature

- No proof that a worker performs useful AI generation; owner #1340/#1342.
- No proof that the machine, listener or application is currently running.
- No privileged live fix or restart, and no automatic deploy on CI success.
- The report explicitly marks `deploy_history_exhaustive=false`: its newest 15 observed deploy runs do not prove an exhaustive history. An ambiguous runner name/ID combination is denied rather than reported as assigned.


## 14 October continuation: integration on current main and status reconciliation

The observer was fast-forward integrated with canonical `main@c2a944deed94ee6cdaadd37e5d0e54e87785502c` through a two-parent source merge commit (no force push, no privileged runtime change). Only four add-only owned paths remain different from main.

The live main SHA changed from `e5a63e3` to `c2a944d`; the newer SHA returned zero legacy commit-status receipts at the time it was queried. **Status absence is not success or a reason to repeat a deploy automatically.** Unknown or truncated `total_count` is denied. If GitHub's latest main deployment conclusion and production status disagree, the result is `STATUS_RUN_CONFLICT`, not an invented green/red production verdict. Active deployments produce `DEPLOY_RUN_IN_PROGRESS` and advice to observe rather than redispatch.

The workflow now tests **every** commit to this dedicated branch, including a commit that only integrates new current-main changes; this prevents an unnoticed stale green proof when the code base changes but the four observer paths remain unchanged.

A passing hosted workflow is strictly source/test evidence. Production/worker generation/SQLite/runner state requires separate live proof. If an owner later merges this observer into main, use a controlled source-only release path that does not accidentally trigger the legacy `workflow_run` production deployment.


## Manual main operator action (source-only rollout)

After this workflow update lands on `main`, navigate to **GitHub Actions →
zCloud deploy-ops read-only exact-main receipt proof → Run workflow**. Select
`main` and start a run as repository owner **Zennay**. No other branch,
actor or trigger can run the diagnostic job. Only a trusted first-attempt
manual main run is accepted; ordinary `main` pushes never trigger this
workflow. The isolated `deployops/manual-main-readonly-proof-20261014-w1`
branch permits source CI while this change is being validated.

The hosted job checks out exactly the triggering commit with persisted
credentials disabled, runs its full source tests, and uses only `contents:read`,
`actions:read`, `statuses:read` to inspect the latest current-main GitHub
receipts. It publishes a bounded sanitized GitHub job summary and 7-day
artifact. It **cannot** mutate a deployment, cancel queued work, restart a
runner, touch SQLite, change service permissions or certify worker generation.
A result of `PRODUCTION_STATUS_ABSENT` is informative, never release approval.

The user-facing workflow remains manual-only on main specifically to avoid
triggering `zcloud-vps-deploy.yml` through an automatic successful main
regression. Do not enable a schedule or `push: main` trigger until the
separately owned production release safety gates have been reviewed.
