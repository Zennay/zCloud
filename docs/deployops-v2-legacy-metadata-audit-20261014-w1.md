# Worker-v2 legacy service audit — private-safe deploy-ops acceptance

Status: **source candidate only**, unmerged, undeployed. Scope: zCloud deploy-ops issue [#633](https://github.com/Zennay/zCloud/issues/633).

## Problem fixed

The former manual diagnostic printed process IDs, complete systemd unit rows, raw service descriptions and service lists into public GitHub Actions logs. The new `scripts/zcloud_v2_legacy_service_inventory_20261014.py` collects four bounded local snapshots and emits only aggregate, allowlisted JSON fields. No process IDs, command lines, unit names, paths, Firefox URLs, prompts, actor identities, stderr or exception details leave the diagnostic.

The script has 5-second command deadlines, bounded input (64 KiB/4,000 rows per command), strict UTF-8 regular-file reads (64 KiB), restricted semantic version syntax, refusal of duplicate manifest keys, and finite counts. A command failure, malformed row, ambiguous service entry, duplicate ID or malformed source returns `null`/incomplete evidence rather than making a reassuring zero claim. The script does **not** call the control-plane HTTP GET endpoints, which can reconcile SQLite (#1341). All reported `mutation_performed`, `production_verified` and `worker_generation_verified` flags remain false.

## Operator procedure (only after integration has passed safety gates)

1. **Do not open a PR or integrate while privileged PR-triggered self-hosted diagnostics remain unresolved:** [#638](https://github.com/Zennay/zCloud/pull/638) and [#1135](https://github.com/Zennay/zCloud/issues/1135) are independent required reviews.
2. Verify the source branch is based on the *current* immutable main SHA. Use the GitHub-hosted focused and complete CI; all jobs must be terminal green at exactly the current branch head before review.
3. After approved source integration, the `workflow_dispatch` trigger requires a trusted owner and triggering actor, main ref, **run_attempt = 1**, full current `confirm_sha`, and explicit `approve_readonly_probe = true` (default false). An old/re-run SHA is rejected.
4. Job admission checks Git checkout SHA, live remote main, runner identity and owner. The observation step independently rechecks remote main immediately before any live process/systemd reads.
5. Interpret `observation_complete=false` as **incomplete evidence**, never an idle/healthy state. Even `observation_complete=true` does not prove a listener works or a production deployment succeeded. `production_verified=false` and `worker_generation_verified=false` are unconditional. Live remediation remains exclusive to [#1317](https://github.com/Zennay/zCloud/pull/1317) and serialized #580/PWQ-41/#1089 owners.

## Safe acceptance

- Test the hosted-only contract, raw-output redaction and adversarial input cases (malformed process row, duplicate unit, ambiguous version/manifest, timeout, symlink, oversize, invalid UTF-8).
- Use the hosted full Python/Node/shell regression as **source proof only**; the branch **does not** authorize a VPS job, SQLite mutation, Firefox state changes, runner restart, queue cancellation or deploy.
- No new privileged auto-trigger, token permission or self-hosted regression has been added. Production release evidence must be collected separately by its exclusive owner.
