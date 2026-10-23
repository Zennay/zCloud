## 23 October 2026 · training-output inode exhaustion is a separate HOLD

The owner-only `/zssh workers` receipt now checks inode capacity at the actual
`/opt/fuckball-training` output path, not only free bytes. This check is
admitted only after the output directory is observed as a stable mounted,
local-block-backed, writable target; root capacity or an unverified mount is
not substituted for the training filesystem.

The receipt reports `inode_observation_attempted`, `inode_status`,
`inodes_total`, `inodes_free`, `inodes_available`,
`inodes_unavailable_percent` and `inode_pressure_hint`. At least 99%
unavailable is `CRITICAL`, 95% is `HIGH`, and 90% is `ELEVATED`.
Unknown inode counters prevent an otherwise eligible 30-GiB storage candidate
from being described as ready. The operator action feed preserves the inode
HOLD/review code even when a separate low-space or mount blocker is also
present. This remains advisory: no training is started, no data is moved and
no runner/service/queue/claim state is changed.

# ChatGPT → GitHub Actions → VPS (no MCP, no Claude)

This is an **independent owner-only control path** in zCloud for the existing
`vps-bb300bba` self-hosted GitHub runner. It does **not** use the unfinished
zSSH public MCP/OAuth/DNS publication or bypass zSSH's release gate.

## How to operate from an ordinary ChatGPT chat

Connect the **GitHub** app, then ask ChatGPT to add one exact command as a new
comment on [zCloud issue #1278](https://github.com/Zennay/zCloud/issues/1278).

Supported comment bodies (one line, no extra Markdown):

- `/zssh status` — bounded host uptime, root-disk usage, memory (read-only)
- `/zssh workers` — privacy-preserving Runner.Worker/Runner.Listener occupancy bands and worker-age bands (read-only)
- `/zssh project cloud` — current deployed zCloud Git revision (read-only)
- `/zssh service zennay-cloud` — systemd state (read-only)
- `/zssh service zssh` — systemd state (read-only)
- `/zssh service zssh-public` — systemd state (read-only)
- `/zssh run write-probe` — write, verify and delete a temporary VPS file
- `/zssh run zcloud-guard-test` — execute the reviewed VPS identity unit test
- `/zssh run zcloud-health-smoke` — run a separate, checked-in Python recipe for a real VPS guard smoke test

Example user request: "Check the VPS via the zSSH GitHub runner route. Comment
`/zssh status` on zCloud issue #1278, then inspect the resulting Actions run
and give me the actual output." ChatGPT can use GitHub's issue-comment and
workflow-run/log tools without needing a terminal connector.

Each accepted owner comment causes the `zSSH ChatGPT VPS runner bridge` workflow
to validate the exact event on GitHub-hosted Ubuntu, then queue a bounded job on
`[self-hosted, zcloud, vps]`. The job rechecks the runner identity (hostname,
user and the existing zCloud guard). A separate GitHub-hosted job posts a result
comment back to #1278 with the Actions run URL. Read the run's sanitized receipt
via GitHub to inspect results. The connector's observed GitHub App ID is
`1144995` (`chatgpt-codex-connector`); GitHub-owned direct comments from
`Zennay` also work. Other authors, GitHub Apps, issue numbers, PR comments,
events, multiline payloads and shell strings are rejected.

## Extending operations safely

This repository is **public**. Commands and Action run logs are visible to
others. Never send passwords, keys, tokens, PII, private file content or
arbitrary shell as commands. The bridge intentionally does not accept generic
`ssh`, `bash -c`, `sudo`, file reads or unrestricted commands.

To perform new VPS work from ChatGPT, have ChatGPT implement the exact operation
as a reviewed, tested *Python recipe* at
`ops/zssh-runner-recipes/<recipe-name>.py` and merge it through the protected
main branch. The fixed parser accepts a lowercase recipe slug, never shell
text or a filesystem path. The runner executes that exact checked-in Python
file as the non-root account with a 20-second deadline, suppresses its raw
stdout/stderr, and returns only a machine-owned success/failure receipt. To
run it, post `/zssh run <recipe-name>` on issue #1278. No additional workflow
or interpreter change is required for each new recipe.
This gives the same self-hosted VPS execution mechanism without exposing an
unreviewed public remote shell. Use dedicated least-privileged service identities
or protected GitHub environments for more powerful recipes.

For private/secret-dependent or arbitrary shell operations, migrate the
controller to a private operations repository, add an explicit approval
boundary and scope the runner to that repository before enabling raw commands.
No GitHub runner is an SSH daemon: the runner executes isolated, bounded jobs
on the VPS itself, so there is no need to open TCP 22 or store SSH keys in
ChatGPT. Interactive terminal sessions are not part of this design.

## Runner occupancy snapshot

The receipt uses the stable schema identifier `zssh.runner-workers.v1` for both
observed and unavailable outcomes. Consumers should branch on this identifier,
treat unknown action codes conservatively, and preserve all explicit
`*_verified=false` and `*_authorized=false` fields.

`/zssh workers` reads only bounded `/proc/<pid>/stat` identity fields and
`/proc/uptime`. It never reads command lines, environments, process IDs in the
receipt, private files, or GitHub tokens; it does not invoke a shell, change
services, or write queue/lease state. The output uses occupancy bands rather
than exact worker IDs, excludes the current `Runner.Worker` when its ancestry
is observable, and marks partial or unavailable observations explicitly.
Worker age bands are an operational clue, not proof that a job is stuck or
safe to interrupt. If the current worker cannot be excluded, the separate
"other workers" field stays unknown.

This is deliberately **not** a task-ownership inventory: it does not map a
process to a branch, PR, Notion claim, or queue lease. A zero-worker observation
is not claim clearance, and the receipt always sets ownership verification,
takeover safety, and release authority to false. Use it as a privacy-safe
capacity hint before the human/Notion/GitHub ownership check.


The receipt also includes `runner_service_cgroup`: counts and enum-only summaries
for `Runner.Listener`/`Runner.Worker` process-name matches inside or outside
`actions.runner.Zennay-zCloud.zcloud-vps-1.service`. The bounded
`/proc/<pid>/cgroup` reader never returns the raw path. Summaries deliberately
end in `comm_only`: `/proc/<pid>/stat`'s process name is not verified executable
identity. `complete_for_comm_matched_processes` means the bounded process scan
and observed cgroup files were complete; it does **not** prove GitHub runner
registration, labels, job assignment, service health, claim ownership, or restart
permission. Partial/inaccessible metadata stays UNKNOWN. No process is stopped,
no service is inspected/restarted, and no queue or lease is modified.


The same object exposes `operator_summary`, a bounded Dutch/English-neutral sentence
for an operator. It names only the observed category and the safe next step: preserve
active work, reconcile with the authorized owner, and never infer takeover or restart
authority from process presence. It is a convenience summary of the enum fields, not
an independent source of evidence.

The receipt also exposes a bounded `operator_action_codes` array for
machine-readable handoffs. Stable codes distinguish unknown runner membership,
process-name matches outside or mixed across the expected service unit, a
zero-worker observation that is **not** claim clearance, and critical/high/
unknown capacity evidence. Partial coverage is reported separately with
`CAPACITY_EVIDENCE_PARTIAL`, even when another field already indicates
critical or high pressure. Every receipt includes
`PRESERVE_ACTIVE_WORK`, `TAKEOVER_NOT_AUTHORIZED`, and
`RESTART_NOT_AUTHORIZED`. `CURRENT_WORKER_NOT_IDENTIFIED` is emitted from the
explicit self-exclusion field, even if a partial scan uses
`partial_observation` as its top-level state; an incomplete scan therefore
cannot hide uncertainty about which worker is making the request.
`NO_PROCESS_MATCH_IS_NOT_REGISTRATION_PROOF` is also emitted when an
incomplete process scan observes zero Listener/Worker matches. An empty partial
scan is not a negative health or registration result. `HOLD_DISK_WRITES_REQUEST_OWNER_INVENTORY` is
advisory when root capacity is critical; it does not identify files or grant
cleanup permission. `CAPACITY_THRESHOLDS_NOT_EXCEEDED_NOT_HEALTH_PROOF`
explicitly prevents a normal capacity snapshot from being mistaken for a
healthy runner or verified GitHub job assignment.

Listener and worker service-cgroup summaries are evaluated independently.
For example, a listener observed inside the expected unit does not hide a
worker observed outside it: the receipt emits `OWNER_RECONCILIATION_REQUIRED`
plus `WORKER_SERVICE_MEMBERSHIP_MISMATCH`. The corresponding listener code is
`LISTENER_SERVICE_MEMBERSHIP_MISMATCH`. Mixed membership in either role also
requires owner reconciliation. The combined human summary prioritizes an
outside/mixed worker finding over a listener-in-unit finding, so an in-unit
listener cannot mask an anomalous worker. A positive per-role outside-unit
counter remains an owner-reconciliation lead even if unrelated process/cgroup
coverage is partial; the receipt still reports partial/unknown coverage and
does not promote that observation to a complete identity finding. These codes
do not prove executable identity or GitHub assignment. These codes summarize the same bounded observation;
they are not a task/claim inventory and never authorize mutation, takeover,
service restart, release, or deployment.



The `/zssh workers` receipt also includes `host_capacity_observation`. It
reads only bounded `/proc/loadavg` and `/proc/meminfo` data plus
`statvfs` capacity counters for the root filesystem and the preconfigured
archive mount. It returns aggregate load, memory/swap, free/used capacity,
threshold warning codes, coverage, and a bounded operator summary. The archive
is counted only if the configured path is a real mount point, not merely a
directory or symlink.

Mount membership is resolved from a bounded `/proc/self/mountinfo` snapshot with exact decoded mountpoint matching; `os.path.ismount()` alone is not treated as authoritative because same-device bind mounts may be missed. Truncated, malformed, unreadable, or multiply matched mount evidence fails closed. The bridge rechecks the path's device/inode and mountinfo membership around the capacity read; if the observed target changes, archive capacity is not credited. The receipt exposes only a bounded detection-basis enum, not mount IDs or source paths.

Warnings use conservative fixed thresholds (root filesystem >=95% used, swap
>=90% used, available RAM <=10%, or maximum load average >=2x detected CPU
count). Machine-readable severity is `critical` for a critically full root
filesystem, `high` for other threshold warnings, `normal` when complete data
has no threshold warning, and `unknown` when the snapshot is partial. A
configured archive mount counts as a possible capacity destination only when
it is a real mount point and reports strictly positive available bytes.
Partial or unreadable fields stay unknown. The summary may recommend an
owner-approved inventory/offload when root capacity is critical; it never
identifies files to delete or move and does not establish encryption, retention,
open-file, runner-health, registration, job-assignment, or causal evidence.
The receipt always keeps cleanup, offload, restart and mutation authorization
false. No shell command, file-content inventory, filesystem write, deletion,
move, service inspection or restart is performed.

## Test & rollback

`python3 -m unittest -v tests.test_zssh_chatgpt_runner_bridge`

The existing zCloud regression smoke also discovers this test. To disable the
bridge, disable its Actions workflow or revert its workflow file on protected
main; unrelated runners and the existing zSSH MCP path are unaffected.


## zSentinel archive-mount evidence

The `/zssh workers` capacity receipt treats the configured `/home/ubuntu/VPS-Archief` path as an expected mount, not merely a directory. If the path is not a detected mount, or its mount/capacity cannot be observed, the receipt sets `coverage=partial`, includes an `evidence_warnings` reason (`archive_mount_not_detected` or `archive_mount_observation_unavailable`), and emits `ARCHIVE_MOUNT_UNAVAILABLE_NO_OFFLOAD`. A detected mount on the same filesystem as root emits `ARCHIVE_MOUNT_NOT_SEPARATE_FROM_ROOT_NO_OFFLOAD`; it is not credited as independent offload capacity even if `statvfs` reports free space. A detected separate filesystem with a measured zero free bytes emits `ARCHIVE_MOUNT_NO_CAPACITY_NO_OFFLOAD`; unavailable capacity counters instead emit `ARCHIVE_MOUNT_UNAVAILABLE_NO_OFFLOAD` and are not misreported as zero space.

The same receipt samples the `statvfs` read-only flag before and after the capacity read. It credits a separate archive filesystem as a possible offload target only when `archive_mount.read_only` is explicitly `false` and remains stable across both observations. A detected read-only mount emits `ARCHIVE_MOUNT_READ_ONLY_NO_OFFLOAD`; missing or changing flag evidence emits `ARCHIVE_MOUNT_READ_ONLY_STATE_UNCERTAIN_NO_OFFLOAD` and leaves offload capacity uncredited. A non-read-only mount flag is necessary evidence, not proof that the calling user can write or permission to move data; owner authorization and file-level checks remain mandatory.

These are **evidence/offload constraints**, separate from the resource-pressure `warnings` and `severity` fields. Missing archive storage does not itself claim high CPU/RAM/disk pressure, and it never authorizes moving or deleting files. If root storage is critical, preserve active work and obtain an owner-authorized storage inventory; do not treat the mere existence of the archive directory as proof that external storage is mounted.


## zSentinel: 30 GiB bulk-storage decision without false training clearance

The existing owner-only `/zssh workers` command now includes
`host_capacity_observation.bulk_storage_advisory`, a bounded, read-only
assessment of the **configured archive mount** only (currently
`/home/ubuntu/VPS-Archief`). It does not assume that this archive mount is
the actual Fuckball/HaxLab training output path. The explicit threshold is
`minimum_free_bytes=32212254720` (30 GiB, binary units).

A complete `state` is one of:

- `mount_missing` / `mount_unknown`: no verified archive mount.
- `root_filesystem_shared` / `filesystem_identity_unknown`: no verified
  separate filesystem; an archive directory is not a separate drive.
- `read_only_mount` / `mount_writability_unknown`: archive mount flags
  cannot support or prove writes.
- `virtual_or_network_mount`: the kernel reports FUSE (including rclone),
  network, or virtual storage. **Not a local block-device training target**,
  even if the provider advertises ample capacity.
- `block_backing_unknown`: no verified block-special device node for the
  configured local filesystem source; owner inspection is required.
- `free_space_unknown` / `below_30_gib`: free-space evidence unavailable or
  below the minimum, even when other mount evidence is valid.
- `capacity_candidate_not_training_authorization`: separate mounted
  filesystem, non-read-only mount flag, verified block-special device source
  hint, at least 30 GiB free. **This is only a capacity candidate, not proof
  of an external physical drive or a permitted training output target.**

Every state has a concise `operator_summary`, a stable
`next_step_code`, and a matching `operator_action_codes` entry (for example
`ARCHIVE_BULK_BELOW_30_GIB`, `ARCHIVE_BULK_REJECT_VIRTUAL_OR_NETWORK`,
`ARCHIVE_BULK_BLOCK_DEVICE_NOT_VERIFIED`, or
`ARCHIVE_BULK_CAPACITY_CANDIDATE_REQUIRES_EXTERNAL_PROOF`).
The backing clue is obtained from bounded exact-target `/proc/self/mountinfo`
filesystem/source data; common local Linux filesystems require an actual
block-special `/dev` source, not a path name alone. Backing classification
is re-observed after the capacity sample. Only the privacy-safe
`filesystem_backing` enum is returned; source device paths, major/minor IDs,
mount IDs, credentials, and raw mountinfo remain private. Malformed, changing,
unreadable and unsupported evidence remains UNKNOWN. The receipt
**always** sets `physical_drive_separation_verified=false`,
`training_output_path_verified=false`,
`training_user_write_permission_verified=false`,
`training_running_verified=false`, `training_start_authorized=false`, and
`archive_offload_authorized=false`.

This operator receipt cannot prove that two mountpoints use physically
different disks, that the actual training directory is on the archive mount,
or that the training user can write to that directory. Those facts require
independent owner-authorized VPS/storage inspection before an actual training
service launch. Do not move existing data, relabel training as active, replace
a running worker, bypass an external-disk preflight, or infer free system-disk
capacity from this receipt. No shell, device writes, service actions, claims,
training jobs or file migration are performed by this advisory.

## zSentinel: actual Fuckball training-output path (separate from archive)

The same owner-only read-only `/zssh workers` receipt now includes
`host_capacity_observation.training_output_observation` for the fixed
`/opt/fuckball-training` path. Unlike the archive advisory, this reports the
**actual known training output location**. It never creates the directory,
remounts it, copies data, restarts a service or starts training.

- If the directory is on the **same filesystem as `/`**, the status is
  `training_output_on_root` and the operator action is
  `HOLD_TRAINING_SYSTEM_DISK`, even when root has hundreds of GiB free.
- Missing, inaccessible, non-directory or symlink paths remain explicit
  `path_missing`, `path_unavailable`, or
  `path_not_directory_or_symlink` with safe owner follow-ups.
- When the directory is on another filesystem, a stable exact mountpoint,
  bounded local block-device backing, a non-read-only flag and **30 GiB free**
  are all necessary for `training_capacity_candidate_owner_proof_required`.
  An unverified mount, FUSE/rclone/network source, read-only mount, low free
  space or changed source gets a specific HOLD/refresh code.
- Source identity (`st_dev`, `st_ino`), direct mount membership, block
  backing, read-only flag and measured capacity are sampled twice around
  `statvfs`. Unstable observations are not credited. The public receipt
  exposes only enums, bounded capacity and booleans, not device/mount IDs,
  inode numbers, paths from kernel evidence or secret content.
- Every training receipt explicitly preserves
  `physical_external_disk_verified=false`,
  `dataset_checkpoint_temp_paths_verified=false`,
  `application_write_permissions_verified=false`,
  `training_running_verified=false`,
  `training_start_authorized=false`, and `mutation_performed=false`.

A candidate remains **HOLD for training** until the real launcher on the
correct host independently verifies a separate *physical* block device,
application-user write access and all model/checkpoint/dataset/log/cache/temp
paths. An observed second filesystem can still be a partition on the system
disk. The existing reviewed launcher and release-owner gate are authoritative.
This diagnostic must never bypass them or claim training is live.

On the new VPS observed on 23 October, the zSSH read-only `df -h
/opt/fuckball-training` command returned the root device `/dev/sda2`.
This is **host evidence at one moment**, not proof of any subsequent runtime
state. The independent migration issue
[Zennay/zCloud#1588](https://github.com/Zennay/zCloud/issues/1588)
contains the operator handoff.

## zSentinel process identity and PID reuse

The current `Runner.Worker` is identified through the bounded parent chain and excluded from the other-worker count using the pair `PID + /proc/<pid>/stat start_ticks`, not PID alone. This prevents a process that reuses the same PID between ancestry discovery and the inventory scan from being silently treated as the current worker. The public receipt exposes only the basis label `pid_and_start_ticks` or `unknown`; it never returns raw PIDs or start ticks. A missing self-identity remains unknown and does not authorize takeover.


## zSentinel root inode pressure

The host-capacity receipt includes root inode totals, free/available counts and percentages alongside byte capacity. It classifies inode pressure separately: `root_inode_pressure_high` at ≤5% available or ≥95% used, and `root_inodes_critical` at ≤1% available or ≥99% used. Critical inode exhaustion emits `HOLD_STORAGE_WRITES_REQUEST_OWNER_INVENTORY`; byte exhaustion retains `HOLD_DISK_WRITES_REQUEST_OWNER_INVENTORY`. If the filesystem cannot report inode counters, the receipt marks capacity coverage partial rather than claiming inode headroom is healthy. Inconsistent inode or archive free/available block counters are also rejected into partial evidence; unavailable counters are never treated as measured zero free space. Stable top-level `operator_action_codes` also surface the observed cause as `ROOT_FILESYSTEM_CRITICAL`, `ROOT_INODE_PRESSURE_HIGH`, `ROOT_INODES_CRITICAL`, `SWAP_PRESSURE_HIGH`, `MEMORY_PRESSURE_HIGH` or `LOAD_PRESSURE_HIGH`; these are advisory signals, not permission to stop or restart work.

Resource-pressure `warnings`/severity remain separate from evidence warnings for missing archive storage. The operator summary preserves both when they occur together, so high CPU/swap pressure cannot hide a missing offload mount, and a missing mount alone does not inflate pressure severity.
