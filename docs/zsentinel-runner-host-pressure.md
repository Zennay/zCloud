# zSentinel — self-hosted runner + host pressure operator workbench

## What has changed

The existing `scripts/zsentinel_runner_triage.py` supports an opt-in
`--observe-host-pressure` on the **intended vps-bb300bba host**. The
same source-bounded owner workbench now shows (1) the existing exact GitHub
run/job read, (2) the current `Runner.Listener`/`Runner.Worker` observation,
(3) the fixed zCloud runner systemd unit's selected state properties, and
(4) the live host root-filesystem/inode/memory headroom. The host sample
is strictly advisory and is recorded alongside the exact GitHub source. This
helps operators triage P0 [zCloud issue #1326](https://github.com/Zennay/zCloud/issues/1326)
without claiming the self-hosted runner is registered, healthy, idle or restarted.

The local observer also runs a **read-only, three-second-bounded**
`/usr/bin/systemctl show` for the single configured unit
`actions.runner.Zennay-zCloud.zcloud-vps-1.service`. It requests only
`LoadState`, `ActiveState`, `SubState`, `UnitFileState` and `Result`;
output is capped at 4 KiB and checked against a fixed property/value allowlist.
It does not invoke service actions, inspect the journal, read unit-file contents,
or expose PIDs, command lines, registration credentials or environment files.
If systemd is absent, denied or times out, the report says **UNKNOWN** and keeps
the separate process observation. The report explicitly labels
`unit_active_listener_not_observed`, `listener_observed_unit_not_active`,
`unit_active_listener_observed`, or `unknown_*` when comparing the two
snapshots. These are observation relationships, not health or permission
states. A unit reported active while the listener is missing (or vice versa)
remains a discrepancy for the authorized owner, never an automatic restart
decision. The HTML/text workbench labels this separately from GitHub
registration and exact-job assignment. For a stale unassigned self-hosted job,
the owner route also differentiates a missing unit, an active unit without an
observed listener, a listener with an inactive unit, and an inactive/failed
unit with no listener. Each route remains advisory and requires the authorized
owner to verify current work, repository registration, labels and access; none
permits an automatic restart or re-registration.

The new `zsentinel_runner_host_pressure.py` reader pins the root directory
with `O_NOFOLLOW` and reads capacity through `fstatvfs`; it reuses
the existing bounded, read-only `/proc/meminfo` memory parser. No queue
SQLite is required, and no secret files, command lines, external archive
directory names, GitHub credentials, worker prompts or process IDs are copied
into the headroom report.

## Run only from the intended VPS (opt-in, no changes)

Choose **one** existing exact GitHub job or bounded discovery. Examples:

```sh
# One limited existing-job discovery + private operator report.
umask 077
python3 scripts/zsentinel_runner_triage.py \
  --discover --observe-host-pressure --format html \
  --output /home/ubuntu/zennay-cloud/private/runner-owner-observation.html

# Exact existing run/job on the same VPS. Use the real GitHub job SHA.
python3 scripts/zsentinel_runner_triage.py \
  --run-id EXISTING_RUN_ID --job-id EXISTING_JOB_ID \
  --head-sha EXISTING_FULL_40_CHARACTER_HEAD \
  --observe-host-pressure --format text
```

The HTML output path is an example and must be a **new** private, create-only
file in an existing owner-private directory. The command does not configure,
repair, enable or dispatch any runner, service, Actions job or database task.

`--observe-host-pressure` is optional and backwards-compatible. If the
local source is not available, the operator receives **UNKNOWN**, not a
zero or healthy result. The tool captures each limited host sample at one
UTC observation instant and never upgrades historical GitHub receipts to
current runner health proof.

## Actionable, not authorizing

- Critical headroom: root space below 2 GiB, root usage at least 95%,
  inode usage at least 95%, or available RAM below 512 MiB.
- Constrained headroom: root space below 4 GiB, root usage at least 90%,
  inode usage at least 90%, or available RAM below 1024 MiB.
- The reported percentages are based on the current observed host only.
  Root fullness alone does **not** prove why a runner is unavailable.
- A separate GitHub Actions job may be unassigned because of required labels,
  runner registration/group visibility, runner health, concurrency or
  account billing; an empty process sample does **not** authorize a restart.
- Only a qualified host/queue owner may decide on cleanup, job cancellation,
  service restart or runner re-registration after checking actual running
  jobs, release writers, disk/backup dependencies, branch protection and
  required authorization.
- Existing exact-head CI/reviews and deploy gates remain mandatory.
  This is a source-only PR feature until merged; no live deployment is claimed.

Implementation:
`scripts/zsentinel_runner_host_pressure.py` and
`scripts/zsentinel_runner_triage.py`. No QA test code, regression suite or
CI workflow was added by this feature.
