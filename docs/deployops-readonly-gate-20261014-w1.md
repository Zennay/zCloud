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
checkout, runs adversarial tests, then makes GET-only requests with
`contents:read`, `actions:read` and `statuses:read`. The live observer's
diagnosis is **not** an acceptance or production-release gate, even if source
tests are green.

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
