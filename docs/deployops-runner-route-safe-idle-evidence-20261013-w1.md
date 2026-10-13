# zCloud deploy-ops: distinguish runner reachability from safe-idle release health

**Status (13 October 2026): source branch only; not merged or deployed.** This is an independent evidence lane, not an alternative producer of trusted production status.

## What the live failures prove

- [Deploy 38417582567](https://github.com/Zennay/zCloud/actions/runs/38417582567) was scheduled on permanent `zcloud-vps-1` (runner ID 21) but failed at backend promotion **and** self-heal rearm. Runner availability is not successful deployment.
- [CPU diagnostic 38417793892](https://github.com/Zennay/zCloud/actions/runs/38417793892) completed on that same runner. This proves historical scheduling, not continuous availability.
- [Deploy 38470693004](https://github.com/Zennay/zCloud/actions/runs/38470693004) failed safe-idle while Supa and zGuard workers still *reported* generation after `drain_command_status=completed`.
- [Deploy 38471675524](https://github.com/Zennay/zCloud/actions/runs/38471675524) separately reached `zcloud-vps-1`, then timed out at safe-idle with workers including Cloud/FTMO reporting generation. **Do not force-stop these workers to make the deploy green.**
- [FuckBall audit 38417002886](https://github.com/Zennay/zCloud/actions/runs/38417002886) used a different `ftmo-research` label and was unassigned at inspection. Do not misattribute it to the already scheduled `zcloud,vps` lane.
- [VPS lane probe 38473312856](https://github.com/Zennay/zCloud/actions/runs/38473312856) contains **two real skipped jobs** with `self-hosted,zcloud,vps` labels and no runner assignment. These were *not dispatched*. They must **not** be counted as queued offline VPS jobs. Waiting/approval/pending jobs are also not yet runner-eligible and are counted separately from truly queued jobs.
- [Safe-idle retry 38473312992](https://github.com/Zennay/zCloud/actions/runs/38473312992) failed when GitHub CLI refused terminal escape sequences from a job-log API response. Live retry workflow is already owned by [PR #1090](https://github.com/Zennay/zCloud/pull/1090); this evidence lane does not edit it.
- **Cross-run exact marker comparison**: read-only GitHub job log inspection found one timestamped `ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED` marker in *each* failed main deploy (#38470693004 and #38471675524). Both had exactly **3/3** blockers reporting `draining` + `drain_command_status=completed` + `generating=true` + latest event `heartbeat`. This is not independent proof of real text changes or a frozen browser. No raw worker identities, chat links or log bodies were published.

## Read-only classification, not recovery

`scripts/zcloud_runner_route_evidence_20261013_w1.py` reads the canonical `Zennay/zCloud` GitHub Actions jobs API using `GH_TOKEN` with read permission. `--recent-deploy` additionally selects the latest **verified** canonical workflow (`workflow_id=369569470`, repo numeric ID `1389227570`, `event=workflow_run`, exact path/main), then resolves each job. The output distinguishes **unassigned**, **assigned but failing**, **completed successfully**, and **unknown step evidence**. A runner assigned at 21:39 does not prove it is available now.

```bash
# Use an already configured, read-only Actions token in your environment.
GH_TOKEN="$MY_READ_ONLY_GITHUB_TOKEN" python3 scripts/zcloud_runner_route_evidence_20261013_w1.py --recent-deploy
```

This prints allowlisted stage categories only. GitHub sometimes omits `steps` for recently failed jobs. In that case the tool returns `step_evidence_missing`, not an inferred stage. It must never claim success on a missing or contradictory step history.

`scripts/zcloud_safe_idle_blocker_evidence_20261013_w1.py` consumes **one operator-supplied job log via stdin** (maximum 256 KiB). It finds exactly one timestamped `ZCLOUD_DEPLOY_SAFE_IDLE_BLOCKED` line, rejects duplicates/future timestamps/nonfinite ages and malformed workers, and counts three classes: generating after a completed drain acknowledgement, still draining without completion, or other active evidence. All identities and log body are suppressed.

```bash
# Inspect only your already retrieved, access-controlled job log.
python3 scripts/zcloud_safe_idle_blocker_evidence_20261013_w1.py < local-job-log.txt
```

The log may itself contain secrets, so do **not** attach or publish raw logs publicly. The tool does not download or retain them, and should not be integrated into privileged workflows as a decision-maker.

## New evidence integrity gates (2026-10-13 follow-up)

The job inspector now binds every paginated GitHub Actions job to the **requested exact run ID**, its canonical `api.github.com` and `github.com` URLs, one immutable `head_sha`, one `run_attempt`, and one workflow name. Cross-run page swaps, inconsistent retry attempts, unexpected URL origins and SHA drift are **hard errors**. When selecting the latest main deployments it also binds the selected run's SHA and attempt to the subsequent job receipts, denying any metadata change in between.

A successful assignment to **any** runner that happens to carry the labels `self-hosted,zcloud,vps` is **not proof** that the actual permanent `zcloud-vps-1` listener received the job. Reports now separate three counters: `assigned_count` (all matching-label assignments), `canonical_vps_assigned_count` (exact name `zcloud-vps-1`) and `unexpected_vps_assigned_count` (assigned under the labels elsewhere). A named, assigned runner still does not prove it is *currently* online.

The log classifier now rejects malformed unhashable drain states, nonfinite JSON even in ignored metadata, and unpaired Unicode surrogates. The runner classifier similarly denies non-string job statuses rather than crashing on JSON arrays/maps. A report from a skipped job with labels is not a runner outage, and a waiting/pending/requested job is not yet queued for a VPS slot. Only a real `queued` job with no runner contributes to `unassigned_count`; a completed skipped job contributes to `not_dispatched_count`, and approval/prerequisite waits contribute to `not_yet_scheduled_count`.

The safe-idle parser reports the timestamped log marker age (`marker_age_seconds`) and `fresh_for_triage`, which is true only for receipts no more than 15 minutes old. **Even recent browser-reported status is not independently verified live worker state**: `independent_live_worker_state_verified=false`, `production_deploy_verified=false` and `authorizes_mutation=false` in every successful result. The historical October 13 blockers must not be used to interrupt currently running chats.

## Critical interpretation limits

- A browser-supplied `generating=true` heartbeat is **not independently observed generated text** and not proof that the tab is actually present. Separate [issue #1342](https://github.com/Zennay/zCloud/issues/1342) owns source/claim-bound progress evidence.
- `drain_command_status=completed` does **not** prove the worker stopped responding or that a browser completed its generation. Diagnosis requires serialized source-owner inspection of the drain handshake.
- `GET /api/runner-targets` and `GET /api/worker-debug` may reconcile SQLite in the current backend: [issue #1341](https://github.com/Zennay/zCloud/issues/1341). The standalone runner classifier does not call those endpoints.
- Neither helper can POST, rerun/cancel jobs, dispatch workflows, repair SQLite, force a tab, restart Firefox, or promote a build. Both always emit `authorizes_mutation=false`; all `production_deploy_verified` fields remain false. Do not relax safe-idle because a test passes.
- [Issue #1135](https://github.com/Zennay/zCloud/issues/1135) keeps generic PR-triggered privileged recovery unsafe; preserve this source in its disjoint branch without opening a potentially mutating PR. Runtime/deploy ownership remains serialized to #580/PWQ-41 and #1089. Full VPS/worker/SQLite and independent release gates must be proven first.

## Reproduction and acceptance

[Dedicated GitHub-hosted workflow](https://github.com/Zennay/zCloud/blob/feat/deployops-runner-route-evidence-20261013-w1/.github/workflows/zcloud-runner-route-evidence-hosted-20261013-w1.yml) performs Python compile, adversarial offline tests, full Python regression, synthetic safe-idle log acceptance, real historical GitHub job evidence and the latest two verified main deploy route reads. The workflow has only `contents:read` and `actions:read`, uses an immutable SHA checkout with persisted credentials disabled, and **never requests self-hosted VPS runners**.

**Review/release conditions:** exact-head hosted validation green; branch diff contains only its own two scripts, two test files, this doc and hosted workflow; owner verifies no conflicting recent PR/PR-less branch; independent reviewers resolve #1135/#1090 and serialized production approval before a separate merge or rollout. No green live deploy claimed.
