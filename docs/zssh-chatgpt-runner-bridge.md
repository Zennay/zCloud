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
