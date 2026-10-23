# Governed workload execution — deployment and ownership contract

## What is implemented in PR #583

A launched command obtains a lease from the canonical zCloud SQLite resource
pool before the child starts. `scripts/zcloud_governed_exec.py` accepts a
project contract, unique execution owner, canonical shared database and explicit
command. After admission it starts the command in its own Linux session, forwards
termination signals to its process group and renews the lease throughout its
run. Normal and exceptional completion both terminate/observe remaining
process-group members **before** releasing admission.

This revision fixes three concrete cases where admission was previously unsafe:

1. **Orphan workload:** the direct subprocess could end while a descendant kept
   consuming resources. The wrapper now stops the process group first. If it
   cannot verify cleanup, the command is **not reported successful**, the
   existing lease is not released and a bounded six-hour retention attempt is
   made for the existing owner. This requires owner incident reconciliation;
   expiration alone does not prove the orphan disappeared.
2. **Duplicate owner:** a second invocation with the same active project/owner
   was previously treated as a renewal and could start a second process for one
   slot. First admission now holds `BEGIN IMMEDIATE` while rejecting an active
   owner and reserving capacity. Subsequent renewals remain authorized only for
   that admitted wrapper.
3. **Exhausted host headroom:** non-protected jobs use an inherited CPU affinity
   mask that leaves the first schedulable CPU out of their mask. They fail closed
   when fewer than two CPUs are available. The child also inherits its canonical
   virtual-address-space limit, while a bounded `/proc` observation watches
   aggregate process-group RSS every two seconds. Missing memory evidence or an
   exceeded group budget stops the workload and requires verified cleanup.

Lease owner keys and command metadata are bounded before SQLite writes. A busy
pool rejection never prints other projects' lease owners or metadata. Resource
release failures cannot be mistaken for a successful run.

## Canonical pool mapping

The actual `project-contracts.json` on current main is the source of the
declared budgets:

| Pool | Slots | Project workload mapping |
| --- | ---: | --- |
| protected | 2 | zCloud control plane, zSSH (zSSH remains external/manual-gated) |
| heavy | 1 | FTMO backtests/research, HaxLab model training, LightUp lab bursts |
| build | 2 | Supa, RaiseAI and other authorized build-class projects |
| disabled | 0 | uLab: existing human/external admission gate; do not silently enable |

The **same physically shared SQLite file** and the **same canonical contracts**
must be accessible to all authorized execution identities, otherwise one-slot
heavy mutual exclusion cannot be established across repositories or VPS hosts.
An isolated test/canary database or a duplicate copied DB **does not** enforce
a shared live pool. Do not weaken database permissions for zSSH or independent
project workers to make admission appear healthy.

Example shape of an authorized supervised service command (not authorization to
start any particular FTMO, HaxLab or LightUp job):

```sh
python3 /home/ubuntu/zennay-cloud/scripts/zcloud_governed_exec.py \
  --db /home/ubuntu/zennay-cloud/history.db \
  --project ftmo --owner unique-supervised-invocation-id \
  --lease-seconds 300 --renew-seconds 60 \
  -- /path/to/approved/workload --its-explicit-arguments
```

A deployed service may reuse a stable owner identity **only after the preceding
process group and lease are gone**. Do not attach a second concurrently running
process to a still-active owner. The supported FTMO TASK-125 unit template is
already rendered using this governed wrapper, but that fact alone is not proof
of its current live unit contents or an actual retained heavy lease.

## Runtime admission hardening · 23 October

The same existing pull request now closes additional lost-lease and
resource-starvation bugs in the real process launcher:

- **Lease generation fencing:** first admission records the returned
  `acquired_at` identity. Both startup/periodic renewal and final release
  use the same project, owner and acquisition generation. A lease which
  expires, disappears or is replaced under a recycled owner ID is never
  silently acquired again by an already-running process. The old executor
  also cannot delete a newer process's lease in its `finally` path.
  A stuck group's bounded retention updates only the original lease row.
- **Pinned database identity:** launchers that set
  `ZCLOUD_RESOURCE_DB` require `--db` to resolve to that same canonical
  path, preventing accidental split-brain pool state when a service
  inherits a different local checkout.
- **Protected host CPU reserve:** non-protected commands cannot use the
  first available CPU on two-core hosts, or the first two on hosts with
  three or more schedulable CPUs. The separate zSSH control route can
  use the second schedulable CPU; actual zSSH service startup remains
  owner-authorized and is not triggered by the governor.
- **Cross-pool host memory admission:** under the existing SQLite
  `BEGIN IMMEDIATE` lease transaction, a non-protected job's declared
  RAM budget is checked against all existing non-protected lease budgets,
  while retaining the sum of the zCloud and zSSH protected project
  memory budgets and a 512-MiB-or-5% host safety reserve. It also checks
  host `MemAvailable`. On cgroup-v2 hosts, inherited `memory.max`
  limits and `memory.current` headroom at each ancestor slice further
  restrict the allowed amount. Invalid/unavailable data refuses new
  non-protected admission before a command starts; it does not kill or
  evict currently active work.
- **Linux crash mitigation:** a direct supervised child receives a
  parent-death SIGKILL setting and a fork-race check, complementing
  the existing process-group signal forwarding. Descendants that call
  `setsid()`, wrapper SIGKILL and remote host crashes still require
  supervised systemd/cgroup containment; do not claim this source
  alone provides full crash recovery.
- **Zombie-safe cleanup:** terminated zombie-only process groups are
  distinguished from a group with genuinely running descendants;
  unreadable or inconsistent process evidence remains fail-closed.

These are **code-path behavior changes**, not a dashboard-only indicator
or an extra release bypass. The quota is based on contract budgets and
observed system headroom, not a claim of precise attributable CPU usage.
No new manual QA suites or production actions were performed for this
source change.



### Admission details and memory-limit separation

The normal `acquire_resource` API can reacquire an expired lease. A running
governor wrapper must **never** treat that as a renewal: the owned generation
must still exist and still be active under the same `BEGIN IMMEDIATE` write
transaction. The same generation condition protects both late cleanup and
the six-hour incident-retention path, so an older wrapper cannot remove or
renew another process's replacement lease.

Before a new non-protected workload starts, its canonical `memory_soft_mb`
is reserved against the current host memory budget plus *all active*
non-protected project leases, regardless of heavy/build pool. Any malformed,
negative or unrecognized lease budget/pool blocks new admission instead of
underestimating reserved RAM. Both zCloud and zSSH contract budgets remain
reserved at host scope, even if those control-plane services are idle.

An inherited systemd cgroup limit is **a separate additional ceiling**:
the wrapper inspects `memory.max` and `memory.current` from the leaf through
its ancestor cgroup-v2 slices. A dedicated FTMO unit's own memory ceiling
cannot be charged again for zCloud/zSSH's *different* services. The global
host reserve remains independent. No limit or cgroup is changed by read-only
admission calculations. The physical host may still run non-governed work;
`MemAvailable` and actual cgroup headroom are consequently both checked
before launching a new job.

On the connected new VPS, unprivileged read-only zSSH inspection showed
approximately 24 GiB physical memory with approximately 7 GiB currently
available and nontrivial system load. This observation is not a live
resource-pool status receipt; the zSSH Unix account is not granted access
to the canonical live SQLite database. Neither permissions nor production
workloads were modified.

## Read-only admission inspection (source-only until release)

The existing governed executor now supports a privacy-safe, read-only
snapshot of the **actual database named by the invoking operator**.
A reader cannot acquire/renew/release any lease through this mode:

```sh
python3 scripts/zcloud_governed_exec.py --inspect-pool \
  --db /home/ubuntu/zennay-cloud/history.db --project ftmo
```

The command requires the exact operator-granted SQLite read access. The
existing `ZCLOUD_RESOURCE_DB` environment, when set, must resolve to the
same path as `--db`. A missing/invalid DB fails closed rather than creating
an empty authority. The inspector uses SQLite `mode=ro` with
`PRAGMA query_only=ON`, and does not initialize tables or prune expired
leases. It emits bounded pool capacity, currently active admitted lease
counts, available slots, an observation timestamp, `pinned_database` and
inconsistency flags. Lease owner identities, metadata and child commands
are never included.

`admission_granted=false` and `runtime_deployment_verified=false` are
intentionally fixed in the inspection result: a point-in-time observer
does not reserve a slot, guarantee future admission, or certify that
FTMO/HaxLab/LightUp actually use the same live pool. A live incident
receipt still requires authorized owner inspection of the deployed
service/cgroup, actual controlled contention and the production
release gate.

## Operator shutdown and exact contract fencing

The governed executor installs SIGINT/SIGTERM forwarding **before**
spawning its child, so an operator stop in the startup/first-renewal
window cannot silently abandon an unmanaged workload. A direct child
that ignores termination is then subject to the existing bounded
process-group TERM/KILL cleanup. Its wrapper returns a signal exit
status rather than running forever while renewing the resource lease.
Cleanup must still prove no runnable group remains before release.

The wrapper captures the canonical compute contract before admission
and applies **that same snapshot** inside the fork to CPU affinity and
memory limits. It rejects first admission if the actual reserved
lease's pool, workload class, CPU or RAM budget differs from that
snapshot. During renewable execution, changing any of those values in
the canonical contract causes renewal to fail closed rather than
silently continuing under inconsistent reservation and process limits.
This is a source-level safety guard, not permission to edit contracts
or restart live research/training.

The pinned production SQLite authority is now opened with
`mode=rw` (rather than SQLite's create-if-missing default), which
rejects a missing file even if a shared mount disappears between
filesystem preflight and the database open. An existing pinned database
must already contain the required `resource_leases` columns; the
wrapper does not initialize a new lease table when the pinned path
points to unrelated or incomplete SQLite contents. Temporary
un-pinned canary databases retain their isolated schema-bootstrap path.

These source guards **do not authenticate** an operator's choice of a
shared database or grant the run user permission to access it. Each
real FTMO/HaxLab/LightUp launcher's approved service identity must use
the same authorized canonical path, and production receipts must still
prove both actual open authority and cross-project exclusion.

## Bounded heavy-pool waiting (opt-in)

A consumer may now request a bounded admission wait without holding the
shared SQLite writer transaction or a resource lease while the pool is
occupied. The default remains fail-fast, and the maximum wait is 3,600
seconds. A process receives the normal lease only when a subsequent
serialized capacity check succeeds. Host and delegated-cgroup memory
headroom are checked *after* that tentative lease reservation but *before*
commit, in the same SQLite write transaction. The new lease is excluded
from the already-reserved-memory sum, avoiding double counting. This order
prevents a still-running pool holder's RAM consumption from incorrectly
terminating a bounded slot wait; genuinely insufficient host memory still
denies admission immediately. A busy-pool timeout retains the ordinary
denied-admission exit status (75).

```sh
python3 scripts/zcloud_governed_exec.py \
  --db /home/ubuntu/zennay-cloud/history.db \
  --project haxlab --owner a-unique-training-invocation \
  --wait-seconds 120 --lease-seconds 300 --renew-seconds 60 \
  -- /path/to/approved/training-command
```

A service owner must select `--wait-seconds` explicitly in an approved
entrypoint; no current FTMO/HaxLab/LightUp production unit was changed.
An owner-identity collision, invalid contract, missing DB, inadequate host
memory headroom, or denied policy **does not** loop: only a genuinely busy
pool is eligible. SIGINT and SIGTERM are observed during waiting, including
before the first child launch. Wakeups are limited to about one per second
and use fresh `BEGIN IMMEDIATE` checks.

This is a bounded retry, **not a globally fair FIFO scheduler or a
no-starvation proof**. Achieving deterministic wait order across projects,
crash-safe fairness and CPU-intensity ranking requires an authorized
integration of the separately owned scheduler/runtime sources. Unrelated
protected pool capacity remains separately reserved.

## New VPS observed running workload · 23 October

Read-only zSSH inspection on
`VM-7eb5ee16-2c71-440d-b7fd-8095d204e930`
found the live HaxLab self-play Node process
`training/haxlab_selfplay.cjs`, launched under
`training/continuous.cjs` in the
`fuckball-ai-coach.service` user scope. Neither observed ancestor
was the governed Python wrapper. The scope already had Linux
`cpu.max=200000 100000` and
`memory.max=3221225472`, i.e. an independent two-CPU quota and
3-GiB memory cap. These bounds help contain that training service,
but **do not establish shared heavy-pool exclusion with FTMO or
LightUp**. The process was not signaled, stopped, restarted or
reconfigured.

The new VPS's zCloud GitHub Actions runner service
`actions.runner.Zennay-zCloud.zcloud-vps-1.service` is present
but disabled/inactive. Runner directory/configuration files exist,
but its registration/labels and current authorization remain
unverified. The owner-only canonical `history.db` is 0600
`ubuntu:ubuntu` and cannot be inspected as the zSSH account;
no permission escalation is authorized. Runner recovery remains
tracked by existing incident
[GitHub #1326](https://github.com/Zennay/zCloud/issues/1326).
Operational service and CI gates are independent of the source merge.

## Shared authority and pool identity hardening

For service owners who pin `ZCLOUD_RESOURCE_DB`, the launcher now records
the existing SQLite file's device/inode identity before opening it and
revalidates that identity after opening and repeatedly during admission
waiting and execution. A missing, swapped or remounted database must
cause a fail-closed exit and the usual process-group/lease cleanup;
the same pathname is not sufficient proof of the same shared
authority. A lost mount is not permission to create a replacement
database. The protected production table must also be a real
`resource_leases` table with its expected columns and
`PRIMARY KEY(project_id, owner_id)`, so a similarly named view or
incompatible lease table cannot be misinterpreted as the authority.

The canonical portfolio invariants are additionally enforced in the
governed runner regardless of a particular project's checkout:
the `heavy` pool can have **at most one slot**, `build` at most two,
`disabled` no slots, and `protected` at least two. Existing
contract fixtures may intentionally omit a pool not used by their
project. A critical project's pool classification is checked when
present: zCloud/zSSH → protected; FTMO/HaxLab/LightUp → the
**same heavy pool**; Supa/RaiseAI → build; uLab → disabled. Widening
a local resource-pool definition or reclassifying a critical project
fails closed. These guards are evaluated on initial admission,
during bounded waiting and on renewal; intentional future policy
changes require the usual reviewed, authorized rollout.

After every tentative new admission, while the same SQLite
`BEGIN IMMEDIATE` transaction still owns the writer lock, the
governor also counts actual active leases in that pool and refuses
the commit if there would be more than one heavy lease or two build
leases. This independently closes the contract-load race where a
different, stale checkout briefly reports a wider pool between
the governor's preflight and the underlying runtime acquire call.
The protected pool is deliberately exempt from this extra global
count gate so a misconfigured non-protected lease cannot prevent
zCloud/zSSH control-plane admission.

None of this proves that **every actual service launcher** has been
converted to invoke the governed runner. In particular the observed
`fuckball-ai-coach.service` training process is not controlled merely
because HaxLab is classified as heavy in the contract. Service owners
must explicitly connect each real process entrypoint to the shared
authority, keep systemd/cgroup containment for detached descendants
and verify exclusion during the authorized live concurrency canary.

## What remains before this milestone is DONE

This is not yet complete production enforcement. The existing FTMO entrypoint,
all HaxLab training and LightUp lab launchers and separately approved Supa/RaiseAI
build entrypoints must each be inspected by their actual owners and wired to the
same canonical lease database (acquire, renew, release, failure cleanup). They
must not be silently restarted or have their active PID trees replaced. HaxLab
training on the new VPS additionally requires the approved **external-disk-only**
storage gate; LightUp active security work retains its scope authorization
boundary. uLab stays disabled until its own human admission gate is satisfied.

The current process-group and RSS/affinity bounds are runtime mitigations:
a descendant that deliberately starts another session, and an unexpected
SIGKILL or host crash of the wrapper, cannot be *proven* contained without a
separately authorized delegated systemd/cgroup supervisor. Before declaring
the acceptance criteria fulfilled, the release owner must inventory that
delegation and require trustworthy cleanup across crashes. No claim is made
that a shared pool works across different machines unless they use one actual
serialized admission authority.

Current source-only work must pass exact-head existing CI, independent review,
protected-main merge, owner-approved serialized production release and the
existing production source/runner identity checks. A **real live resource
receipt**, physical admission observations from each configured entrypoint
and verified control-plane responsiveness are still needed before moving the
Notion/queue milestone to Done. Do not create fresh QA tasks or run manual
QA/regression suites to fill those gaps; existing required CI is unaffected.

## Recovery contract

If admission or cleanup becomes uncertain: do not bypass the heavy pool, do not
delete leases or restart active workers to make a demonstration appear green.
Preserve processes and evidence; reconcile process/cgroup identity and lease
ownership through the authorized service owner, then handle failed admissions
without touching unrelated protected control-plane services. A branch or pull
request is not a live installation.
