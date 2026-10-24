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

## Interim legacy self-play admission veto (not service migration)

The connected new VPS had a separately managed, already running
HaxLab/Fuckball coach under `fuckball-ai-coach.service`. Its visible
process tree included Node `training/continuous.cjs` and
`training/haxlab_selfplay.cjs`, **without** a zCloud governed-executor
parent or a confirmed shared lease. The service has its own 2-CPU quota
and 3-GiB memory cap, but those systemd limits alone do not prevent an
FTMO or LightUp heavy workload from running simultaneously.

To avoid admitting competing new heavy work during that migration gap,
the same governor now applies a **conservative, read-only Linux /proc
veto** to every new `heavy` pool admission: it inspects the observed
`fuckball-ai-coach.service` Linux cgroup membership, including surviving
Python/Node descendants with changed command identities, and additionally
recognizes the observed Node self-play / continuous-training script names
if the process moved into a different scope. If an instance exists outside
confirmed lease coordination,
the attempted new heavy admission rolls back without launching a child.
With `--wait-seconds`, it checks again within the bounded window,
sleeping outside the SQLite transaction and holding no leased slot.
The source does not signal, stop or take over that legacy coach.

The legacy admission veto applies to **canonical live authorities** only:
launchers with `ZCLOUD_RESOURCE_DB` pinned and launchers using the
repository's default `history.db`. A deliberately separate, explicitly
specified unpinned SQLite database is an isolated canary, not an
admission decision about live research. It must remain able to check
the lease mechanics even while legacy training is active on the same
physical runner. The read-only `--inspect-pool` output now reports
`legacy_detection_scope`, plus `legacy_unleased_training_detected`
(`null` when not checked for an isolated canary), and
`effective_remaining` alongside raw `remaining`.
`source` also explicitly distinguishes `readonly_canonical_pool_snapshot`
from `readonly_isolated_canary_snapshot` and reports
`canonical_live_authority` as a boolean.
`other_unleased_heavy_jobs_verified_absent=false` is always reported,
because the known HaxLab fallback is not a complete inventory of
FTMO/LightUp/unknown heavy work. **Never interpret an isolated-canary
snapshot or provisional remaining slots as live admission proof.**
A new production service must pin the shared DB; choosing an alternate
private unpinned DB does not grant production safety or release approval.

**Limitations:** this temporary scope/filename fallback recognizes
only the observed `fuckball-ai-coach.service` unit and two Node script
entrypoints. It cannot discover unknown FTMO/LightUp jobs, other
HaxLab unit names, hidden process namespaces or training on another host.
Consequently it does **not** prove one shared heavy pool. A full
authorized migration to the canonical SQLite lease owner, together
with supervised systemd/cgroup containment and a live concurrency
receipt, is still required. Never mark the release as complete based
on this interim veto.

## Dedicated live service cgroup ownership (crash and detach gate)

A production **heavy** launcher with `ZCLOUD_RESOURCE_DB` pinned now
requires that its own Linux cgroup-v2 path identify a dedicated
`.service` unit, and that its unit subtree contain no live process
other than the governor wrapper **before** opening the shared database
and attempting a new lease. This prevents a restarted service from
admitting another heavy job beside a surviving, detached worker after
a preceding wrapper crash and lease expiry.

The same pinned heavy-service admission additionally checks its
actual Linux cgroup-v2 `cpu.max` and `memory.max` files. Both must
contain numeric kernel-enforced limits no higher than the project's
declared `cpu_soft_cores` and `memory_soft_mb`. The CPU/RAM limit
preflight repeats before every still-active lease renewal, so a later
systemd quota widening cannot be silently treated as the original
bounded admission. Missing/unlimited limits, non-dedicated service
scopes and malformed values deny startup
rather than relying on per-process `RLIMIT_AS` or CPU affinity alone.
This is important for multi-process FTMO backtests, HaxLab training
and LightUp lab bursts, which could otherwise exceed their aggregate
memory budget despite every individual child staying under its own
soft address-space limit.

**Known rollout mismatches, owner actions required:** the existing
FTMO recovery workflow's rendered unit template declares
`CPUQuota=500%` for a contract with `cpu_soft_cores=4`, and does
not show a `MemoryMax` declaration. The separately running
`fuckball-ai-coach.service` was observed at a two-CPU quota and
3-GiB memory max, while the **haxlab** contract has one CPU and
3,072 MiB. Neither current service can be counted as a successful
governor deployment under these new hard-budget preflights without
the respective owner's reviewed, authorized unit change.
The FTMO recovery workflow is separately owned (including retirement
work), and the active HaxLab training service is owned by the separate
external-disk migration. This PR does not change either unit or
interrupt either service.

On exit, the wrapper first stops and observes its own process group.
For pinned **heavy** service commands only, it then reads the entire
dedicated unit's `cgroup.procs` subtree. This additional exclusive-
service requirement does not apply to protected zCloud/zSSH work or
normal build jobs: they may legitimately share a worker service scope,
and denying their normal process-group cleanup could exhaust their
separately protected capacity. A process which called
`setsid()` and left the original process group but remained in the
systemd unit prevents successful release of the heavy lease.
Unreadable cgroup membership, nested/ambiguous cgroup paths, and
non-dedicated (shared runner) scopes are **not** evidence of cleanup.
The code does not signal unrelated processes or modify systemd
settings. The presence of an escapee causes the existing
incident-retention path to retain its original still-active lease,
then report incomplete cleanup rather than claim an ordinary success.

**Release caveat:** when a lease has already expired, the wrapper
cannot resurrect its original generation merely to hold a slot.
Absolute crash isolation still requires the service owner to use
appropriate KillMode/control-group cleanup, bounded memory/CPU
cgroups and an authorized deployment. A hidden process outside the
service's delegated cgroup or on another host is not detected. The
new checks are source-only and do not authorize restarting active
HaxLab/Fuckball training or changing FTMO/LightUp services.
Unpinned isolated canaries retain their existing process-group
lifecycle checks.

## Canonical default history.db is live even without environment pin

The full live heavy-workload service ownership policy now applies to **both**
the explicitly pinned `ZCLOUD_RESOURCE_DB` path and the checkout's
canonical default `history.db` path. Previously the HaxLab migration
veto recognized the unpinned canonical path as live, but hard
`cpu.max`/`memory.max` admission/renewal checks and entire-systemd-unit
drain only applied when the environment variable was present. That
inconsistency could allow a real default-path FTMO/LightUp heavy
launcher to bypass the dedicated-service and orphan-cleanup gates.

The default canonical file also now participates in the exact
`(st_dev, st_ino)` identity check, SQLite non-creating `mode=rw`
open and existing `resource_leases` table/primary-key validation.
Consequently an omitted environment pin is no longer permission for
a default-path workload to recreate an empty split-brain pool when
the original database disappears. Heavy-service CPU/RAM bounds are
rechecked before renewal as well as at admission.

Explicitly separate unpinned `--db` values remain **isolated
canaries only**. Their success is never proof of real admission or
permission to start autonomous production research. Actual service
owners should still configure one pinned canonical shared database
and a dedicated bounded cgroup. Live workers must not be restarted,
stopped, or reconfigured merely to prove the source checks.

## Expired lease and detached-process recovery (23 October)

Before a new `heavy` slot is admitted, the governed executor now reads
**up to 256 expired heavy lease records** under the same
`BEGIN IMMEDIATE` SQLite write transaction used for admission,
*before* the shared runtime prunes expired records. For a record
with known supervised child `pid`, it checks whether the original
process group has any live non-zombie members. The lease metadata
also now captures its dedicated Linux `service_cgroup` when available,
so a still-running descendant that called `setsid()` cannot be
mistaken for a stopped heavy job merely because the original PGID
disappeared. Any known live process or unverifiable/inconsistent
recovery evidence refuses admission; no process is signaled or killed
by this inspection.

The initial lease metadata for canonical live heavy jobs now records
the **own dedicated systemd-service cgroup before spawning the child**,
even while there is no supervised child PID. If the wrapper crashes
between `Popen` and the first PID-bearing renewal, a later admission
can inspect this service scope for detached children; an old
governor-executor record with no PID and no trusted service-cgroup
identity is treated as **ambiguous and refused** rather than silently
recycled.

A canonical live heavy lease also records the originating Linux
`host_boot_id` (one random UUID per boot) alongside its initial
service-cgroup scope, before starting the child. The expired-lease
inspector checks this origin identity **before** examining any local
PID or cgroup: a lease from a different host/boot, or an older
governed record without a verifiable origin, cannot be recycled on
the assumption that the *current* machine has no matching process.
This prevents a new VPS from interpreting its own empty `/proc`
as proof that an orphaned worker on another VPS has stopped.
Cross-host lease recovery still needs authorized operator evidence;
a reboot/boot-ID mismatch is not itself proof of a clean release.
This origin check now covers **any expired canonical heavy lease that
carries a child PID or service-cgroup identity**, including rows
written by older or differently labelled admission tools; a missing
executor label must not make a foreign PID safe to reuse. A legacy
PID-bearing record without `host_boot_id` fails closed until its
original owner proves a clean shutdown and the runtime performs a
reviewed, fenced recovery. Records from unrelated writers without any
child process identity remain only provisional, never proof that
unleased heavy processes elsewhere are absent.
Deliberately separate, unpinned canary SQLite databases continue
using their own local process checks and do not claim shared-pool
protection. Synthetic legacy lease reservations not owned by the
governor retain the old pre-launch handling.

The incident-retention code preserves the original `pid` and
`service_cgroup` in its bounded private SQLite metadata along with
the cleanup-failure marker. Previously it discarded the PID, making
subsequent orphan detection impossible after the retained TTL expired.
The census returns a generic reason rather than leaking PIDs, lease
owners, commands or process paths.

**Important coverage limit:** the underlying shared runtime's
`_cleanup_expired` method can DELETE an expired heavy row from
*other* callers (for example an unrelated lease acquisition or a
status call which prunes). If the last durable evidence is deleted,
the wrapper cannot reconstruct its orphan from this check alone.
Cross-project crash recovery is **not fully delivered** until the
runtime lease writer owned by [PR #1547](https://github.com/Zennay/zCloud/pull/1547)
implements an agreed liveness-aware/durable recovery policy and
the authorized service owners wire the real launchers to one DB.
That owner has received a concrete integration note; its code
has not been taken over here. Expired metadata lacking a supervised
child PID identifies only a pre-launch reservation and is not proof
that an unknown process is absent; the separate known-legacy coach
veto and dedicated-unit admission gates still apply.

No new tests/QA suites were created or run for these source changes.
Neither live SQLite nor active VPS workloads were modified.

## Current exact-head CI routing blocker · 23 October

Read-only inspection of all **10** currently queued/pending
`main` workflow definitions used by PR #583 found that they all
request a self-hosted GitHub runner. Most use
`runs-on: [self-hosted, zcloud, vps]`; the dashboard workflow also
uses the generic `self-hosted` label. Even workflows titled
`hosted-only` and `hosted read only` actually require
`[self-hosted, zcloud, vps]` in their checked-in source.
No runner assignment was visible for representative queued jobs.

On the connected new VPS, the exact
`actions.runner.Zennay-zCloud.zcloud-vps-1.service` is
`inactive/dead/disabled`. Other project runner processes remain
active and must not be interrupted to work around this. The connected
zSSH policy explicitly grants **only**
`zennay-cloud.service` as an inspect/restart target and does not
authorize restarting/re-registering the GitHub Actions listener.
PR #1592 owns serialized new-VPS migration safety; the
authorized runner owner must reconcile its registration/labels and
start the correct listener, then prove exact-head job assignment.
No workflow was relabelled and no runner changed in this PR. The
workflow titles are not an assertion that GitHub-hosted capacity
has failed; the YAML labels are the actual runner requirement.

## Dedicated heavy cgroup memory-headroom correction

Previously the admission guard used `memory.max - memory.current`
as the free headroom **inside the same dedicated heavy service unit**,
then required that remainder to be at least the *entire* declared
workload budget. With a correctly bounded FTMO unit
(`memory.max=4608 MiB`) and even 64 MiB consumed by the governor
wrapper before the child starts, the old formula reported only
4,544 MiB free and always denied a 4,608-MiB job. This made the
required hard service cap and the admission guard contradict each
other.

A verified dedicated canonical heavy unit now treats its **leaf
`memory.max` as a ceiling on the total unit memory**, which already
includes the wrapper's existing usage. It no longer subtracts the
same leaf's `memory.current` when comparing that whole-service
ceiling to the declared budget. The calculations still check
`memory.max-memory.current` headroom in every **ancestor** cgroup
which can contain unrelated work, plus host `MemAvailable`, reserved
zCloud/zSSH control-plane RAM, aggregate active non-protected leases,
and the kernel-enforced leaf CPU/RAM limit at admission and renewal.
Shared build/canary scopes continue to subtract their leaf's current
memory because unrelated processes may also occupy that scope.
For stricter heavy service caps below the project soft RAM budget,
the total project budget remains reserved in the portfolio RAM check.
For local cgroup admission, the needed RAM is the smaller of the
project budget and the **dedicated leaf** memory ceiling, **not** the
smallest inherited ancestor memory ceiling. An ancestor with only
2,048 MiB total available must therefore reject a 4,608-MiB heavy
service even if its own parent reports 2,048 MiB free; interpreting
that ancestor as a deliberately stricter leaf would incorrectly
permit the workload. A genuinely stricter 3,072-MiB **leaf** on a
4,608-MiB project remains enforceable when the ancestors have adequate
headroom; it is not a license to oversubscribe the host.

Verification was isolated: the exact immutable PR-head Python helper
was executed with an in-memory fake cgroup tree and no VPS database
access. With a 4,608-MiB service ceiling and 64 MiB wrapper usage,
dedicated admission headroom was 4,608 MiB; shared scope headroom was
4,544 MiB. When the parent slice had only 3,500 MiB free, dedicated
headroom was reduced to 3,500 MiB. In an additional read-only exact-source fixture, the helper
returned `dedicated=(4608,4608,4608)`,
`shared=(4608,4544,null)`,
`ancestor_2048=(2048,2048,4608)`, and
`stricter_leaf_3072=(3072,3072,3072)` (limit, available
headroom, dedicated leaf ceiling; units MiB).
These are focused arithmetic checks, **not** existing CI,
a complete integration test, or a live
FTMO/HaxLab/LightUp governor canary.

## Exact-head regression execution · 23 October

The existing `tests/test_zcloud_governed_exec.py` module now includes four
targeted regression cases for dedicated-heavy versus shared/ancestor
cgroup memory arithmetic, unknown-origin expired heavy PIDs, detached
same-service workers and PID-less pre-renewal crash recovery. No
additional test framework or workflow was introduced.

A real isolated test execution against the immutable current PR-head
executor and tests, with `project_runtime.py` and
`lane_generator.py` from the recorded `main` source, completed:
**8 tests, 0 failures, 0 errors**, including the four existing
execution/lease tests. The test harness used an automatically cleaned
temporary directory, its own in-memory or temporary SQLite and
lightweight bounded subprocesses, **not production `history.db`**.
This is separate from queued GitHub Actions CI and not a live shared-pool
concurrency receipt.

The first run identified a real host compatibility bug:
`/sys/fs/cgroup/memory.max` is absent at the cgroup-v2 **mount root**
on the connected Linux VPS, although `/sys/fs/cgroup/cgroup.controllers`
and subordinate memory controls exist. The earlier source tried to
read the root's `memory.max` unconditionally and three original
execution tests failed. The executor now treats a missing **mount
root only** `memory.max` as unlimited. Missing or unreadable memory
controls at a real child/ancestor cgroup still fail closed. An
additional test fixture verifies both root-present and root-absent
cases. After correcting that source defect and fixture, all eight
targeted cases passed.

## Cross-process heavy-slot handoff and deadline regressions

The existing `tests/test_zcloud_governed_exec.py` module now
exercises actual two-process, one-heavy-slot contention against a
shared **temporary** SQLite file. One process keeps an FTMO lease
open while a separate Python interpreter invokes the HaxLab governor
with `wait_seconds=5`, using the same temporary test contract.
The test proves no contender child starts during occupancy, then
releases the first lease, observes the contender's successful child
start and exit, and verifies no heavy lease remains.

A second test holds the initial lease through the entire bounded
`wait_seconds=1` interval and verifies exit code 75, no child
launch and the original holder's lease still present. This uses a
non-mutating SQLite row count compatible with the *actual current
main*, not the still-unmerged runtime PR's diagnostic API.

Both tests join the four earlier execution regressions and four
earlier safety regressions in the **same existing test module**.
A fresh isolated exact-head invocation after these changes reported
**10 tests, zero failures and zero errors** on GitHub head
`22837139d9f6b258616abc5bfd9d2d384a95771a`,
with actual `main@723726e0d9c8eb3f84d1b365f1204dae5e6475e0`
runtime and lane-generator modules. All subprocesses and the test
database were within an automatically cleaned temporary test tree.
No production `history.db`, active training unit, or service
configuration was modified. This verifies local concurrency behavior
but is **not** a production cross-project or cross-host canary,
does not replace GitHub Actions checks, and does not prove
cross-writer expired-lease reconciliation in runtime PR #1547.

## Explicit stop and over-budget cleanup regressions

The existing executor test suite now covers two additional process
lifecycle safety conditions against an **isolated temporary SQLite
database**, with a bounded test-only sleeping child:

- **SIGTERM while running:** send SIGTERM to the test governor after
  its child has started and the temporary lease exists; verify the
  governor exits with `128+SIGTERM`, the child no longer executes and
  the owned lease row is removed only after cleanup.
- **Aggregate RSS over budget:** mock one `_group_rss_bytes` reading
  above the heavy project's allowed RAM (no actual memory stress).
  Verify the governed execution returns 75, the sleeping child has
  terminated and its lease was removed. Any short-lived zombie is
  treated as non-running rather than new computation.

Running the **15 existing and extended regression cases** from the
immutable GitHub executor and tests, with `project_runtime.py` and
`lane_generator.py` from the exact recorded main baseline, completed:
**15 passed, 0 failed, 0 errors, 0 skipped**. The test harness used
an automatically deleted temporary worktree and SQLite. This proves
local test behavior; it does **not** replace queued GitHub Actions CI,
approved shared production DB migration, complete remote-host
crash recovery or the real FTMO/HaxLab/LightUp concurrency canary.

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
