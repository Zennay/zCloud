# zCloud P0 runner registration evidence — source-only, no runtime authority

This add-only, owner-disjoint lane supports issue #1326 / PWQ-343. It does not modify
PR #1317 listener recovery, PR #1497 approval-vs-listener classification,
PR #1503 CPU queue provenance, or any production workflow.

## Safe capture and local use

An **authorized operator** retrieves the repository-scoped GitHub REST
`GET /repos/Zennay/zCloud/actions/runners?per_page=100` response from an
administratively authorized channel. Do **not** place API keys, headers,
process arguments or the raw runner inventory in PR logs/artifacts. This tool
accepts only a complete single-page response (<=100 entries), supplied on stdin:

```json
{"repository":"Zennay/zCloud","request_url":"https://api.github.com/repos/Zennay/zCloud/actions/runners?per_page=100","method":"GET","http_status":200,"captured_at":"2026-10-16T17:59:30Z","response":{"total_count":1,"runners":[{"id":21,"name":"zcloud-vps-1","status":"offline","busy":false,"labels":[{"name":"self-hosted"},{"name":"zcloud"},{"name":"vps"}]}]}}
```

`python3 scripts/zcloud_runner_registration_evidence_20261016_w33.py < operator-private.json`

The output is deliberately anonymous: it contains **no runner IDs, endpoint
credentials, other runner names or raw JSON**. It classifies `RUNNER_NOT_LISTED`,
`LABEL_MISMATCH`, `RUNNER_LISTED_OFFLINE`, `RUNNER_LISTED_BUSY`,
`RUNNER_LISTED_ONLINE_IDLE`, or `EVIDENCE_UNAVAILABLE`. **Every invocation exits 2, even when the claim parses cleanly**: a parseable unauthenticated snapshot must never turn a CI/release condition green solely from process exit status.
An outdated, partial, foreign, malformed or ambiguous inventory fails closed.
The operator must also record the exact HTTPS request URL, GET method and HTTP
200 status alongside the JSON response; a wrong repository path, host, query,
method or status is denied. These fields are **operator assertions**, not signed
origin evidence: the checker deliberately cannot authenticate GitHub itself.
The JSON report includes an allowlisted `reason_code`, such as
`unbound_api_response`, `stale_observation` or `incomplete_runner_inventory`,
without copying any raw input. A correctly parsed claim uses
`reason_code=diagnostic_only` and **still exits 2**; unknown exceptions are
collapsed to `unknown_error` rather than exposing their messages.


Even an apparent positive classification is only an **unauthenticated,
operator-supplied claim**: the tool cannot validate the origin of the JSON,
runner groups, current listener activity, whether a job can actually be
assigned, or whether production is healthy. Every output includes
`independent_registration_verified=false`, `runner_group_access_verified=false`,
`service_idle_verified=false`, `restart_authorized=false`,
`dispatch_authorized=false`, `production_deploy_verified=false`.

## Owner handoff

1. Confirm runner registration, group/repository visibility, labels and
   GitHub approval/concurrency separately from trusted current data.
2. If the runner is absent/offline, inspect zCloud listener + Runner.Worker
   and OOM/journal from an **independently reachable authorized host**.
3. Preserve any active worker. Recovery changes remain solely with PR #1317
   owner, after documented safe-idle / exact-main admission and negative tests.
4. Verify an actual zCloud job has `runner_id>0` and succeeds before declaring
   recovery. No queue sweep, runner restart, service write or production deploy
   is permitted by this standalone source-only tool.

Tests: `python3 -m unittest discover -s tests -p 'test_zcloud_runner_registration_evidence_20261016_w33.py' -v`.
No self-hosted job or privileged PR workflow should be attached while
issue #1135's unsafe PR-triggered recovery remains open.
