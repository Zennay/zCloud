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
