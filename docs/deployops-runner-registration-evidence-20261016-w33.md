# zCloud P0 runner registration evidence — source-only, no runtime authority

This add-only, owner-disjoint lane supports issue #1326 / PWQ-343. It does not modify
PR #1317 listener recovery, PR #1497 approval-vs-listener classification,
PR #1503 CPU queue provenance, or any production workflow.

## Operator-side fixed GET — without raw JSON files

On a trusted operator host with the **GitHub CLI** installed and authenticated
using an account/token with read-only repository *Administration* access:

```bash
python3 scripts/zcloud_runner_registration_collect_20261016_w33.py
```

The command makes exactly one fixed authenticated `gh api --hostname github.com
--method GET` request for the canonical runner name `zcloud-vps-1`. It accepts
no user-supplied URL, process name, label, shell command, token or arbitrary
arguments. It neither writes the API response to disk nor echoes it to stdout
or stderr. The CLI stdout is read incrementally with a strict 256 KiB
memory ceiling and 12-second monotonic deadline (instead of accepting an
unbounded subprocess.run capture). Overlarge or timed-out replies terminate
the subprocess and its **private Linux process group** before any
classification (without killing unrelated worker or browser processes).
The descendant-process cleanup is tested with a hosted-only subprocess
that would otherwise leave a delayed marker behind. A blocked request remains
`EVIDENCE_UNAVAILABLE` but now includes a fixed, secret-safe `reason_code`:
`github_cli_not_installed` means install/sign in to the trusted `gh` binary;
`github_runner_read_failed` means the GitHub CLI request failed for an
**unknown** reason (auth/Administration **read** permissions, API availability,
or network); the diagnostic does not claim it was an access denial; `github_runner_read_timeout`
means check the operator's GitHub network reachability; and
`untrusted_runner_response` means the retrieved snapshot could not be accepted.
Raw error messages, stderr, tokens, runner IDs and HTTP bodies are **never**
printed. The fixed error category alone does not prove the cause or authorize
repair. The timeout, oversized response, absent CLI, nonzero CLI exit and
stale/ambiguous payload all remain safe-denial cases.

**Exit status 2 is deliberate for every result, including
`RUNNER_LISTED_ONLINE_IDLE`**: nothing here may be used as a successful
release gate. This process is not run in GitHub Actions or by an autonomous
worker; it is a separate, operator-controlled administration read. The
`http_status=200` wrapper reflects successful JSON retrieval by the
trusted `gh` command, but does **not** independently authenticate GitHub,
verify runner-group entitlement or establish VPS service/Worker idleness.

Only copy the sanitized JSON classification to an incident note. Preserve
any active Runner.Worker and never restart the listener, cancel queued jobs,
start a deploy or change repository settings based on this tool alone.

## Safe capture and local use

An **authorized operator** retrieves the repository-scoped GitHub REST
`GET /repos/Zennay/zCloud/actions/runners?name=zcloud-vps-1&per_page=100` response from an
administratively authorized channel.
The `name` filter minimizes exposure of unrelated registered runners; a
filtered reply containing another runner name is rejected instead of being
mistaken for a complete absence check. This also avoids a repository-wide
multi-page listing when only the canonical zCloud listener matters.
The canonical registration must additionally report `os=linux` and
`ephemeral=false`. Routing labels must preserve GitHub's actual metadata:
`self-hosted` is a `read-only` default label; `zcloud` and `vps` are
`custom` labels. A missing/invalid label type, or a wrong type for a required
routing label, produces a deny-only diagnostic. The realistic GitHub response
may also contain `Linux` / `X64` read-only labels and `version` metadata,
which are handled without treating them as deployment authorization.
This contract matches the public GitHub REST runner example (2026-03-10):
https://docs.github.com/en/rest/actions/self-hosted-runners?apiVersion=2026-03-10 . Other operating systems, missing lifecycle fields or
an ephemeral lookalike are denied before any online/idle conclusion. Do **not** place API keys, headers,
process arguments or the raw runner inventory in PR logs/artifacts. This tool
accepts only a complete single-page response (<=100 entries), supplied on stdin:

```json
{"repository":"Zennay/zCloud","request_url":"https://api.github.com/repos/Zennay/zCloud/actions/runners?name=zcloud-vps-1&per_page=100","method":"GET","http_status":200,"captured_at":"2026-10-16T17:59:30Z","response":{"total_count":1,"runners":[{"id":21,"name":"zcloud-vps-1","status":"offline","busy":false,"os":"linux","ephemeral":false,"labels":[{"id":5,"name":"self-hosted","type":"read-only"},{"id":21,"name":"zcloud","type":"custom"},{"id":22,"name":"vps","type":"custom"}]}]}}
```

`python3 scripts/zcloud_runner_registration_evidence_20261016_w33.py < operator-private.json`

The output is deliberately anonymous: it contains **no runner IDs, endpoint
credentials, other runner names or raw JSON**. It classifies `RUNNER_NOT_LISTED`,
`LABEL_MISMATCH`, `RUNNER_LISTED_OFFLINE`, `RUNNER_LISTED_OFFLINE_BUSY`,
`RUNNER_LISTED_BUSY`, `RUNNER_LISTED_ONLINE_IDLE`, or
`EVIDENCE_UNAVAILABLE`. When GitHub claims a runner is **busy**,
preserve any potentially active worker before considering label edits
or service actions: a simultaneous `offline` + `busy` claim takes precedence
over ordinary offline or missing-label diagnostics. The sanitized
`routing_labels_match` field is `true`/`false` when a runner is present
and `null` when absent. None of these observations authorize a restart. **Every invocation exits 2, even when the claim parses cleanly**: a parseable unauthenticated snapshot must never turn a CI/release condition green solely from process exit status.
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
