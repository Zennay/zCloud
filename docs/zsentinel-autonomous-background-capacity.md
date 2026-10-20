# zSentinel autonomous background capacity (20 Oct 2026)

This source extends the existing Ubuntu user-sidecar without launching new workers. An
**existing** `zcloud-background-priority.timer` evaluates live host pressure once a
minute, then changes **only** `fuckball-ai-coach.service`'s user-scope
`CPUQuota`/`CPUWeight`. All bands leave some CPU for training. The tuner does
not edit models or forecasts, manage FTMO trades, claim jobs, dispatch workflows,
move files or bypass a merge/deploy gate.

## Runtime behavior

| Observation | CPUQuota / CPUWeight | Meaning |
| --- | --- | --- |
| zCloud GitHub job worker observed, load > 1.35× affinity cores, <2 GiB MemAvailable, swap >=80% used, root unavailable >=90%, **or incomplete /proc, memory, load or root-disk telemetry** | 60% / 25 | Yield strongly, never assume unknown observations mean idle. |
| Other GitHub job worker observed, load >0.85× cores, <3 GiB MemAvailable, swap >=60% used, or root unavailable >=85% | 100% / 40 | Yield moderately. |
| None of the above | 175% / 70 | Allow bounded idle background training. |

A process is a job worker only if `/proc/*/comm` is `Runner.Worker` and
its command line is present. An idle listener alone is not a running job.
Disappearing processes during the scan are ignored. If a still-running process
cannot be inspected, or a Runner.Worker has no readable argv, source coverage
is incomplete and the conservative band applies. A failed whole-/proc
observation likewise cannot be treated as proof of idle runner capacity.
The `zcloud_runner_active` field is a **path marker**, not proof that an
AI browser worker or merge job is active. Load, memory, swap and root disk
are independent capacity indicators; CPU throttling does not actually free
swap, disk blocks or repair a runner.

On each timer invocation, the tuner reads the **actual** user-systemd
`CPUQuotaPerSecUSec`, `CPUWeight` and `ActiveState`, rather than assuming an
old saved JSON decision is still installed. If the active service already
has the selected values, no control write is necessary; otherwise the tuner
changes only the existing user-scope properties and reads them back before
claiming `applied_verified=true`. A failed systemctl update or read-back
never publishes a false verified decision. If the coach is inactive, the
tuner does not start it: it writes `unit_active=false` and
`applied_verified=false` for operator visibility. The read-only snapshot and
selected CPU policy are saved through a private atomic 0600 temporary file
under `$XDG_RUNTIME_DIR`; rename replaces the prior state. State stores only
numeric capacity and boolean runner observations, not process command lines,
full filesystem paths or credentials.

## Read-only decision preview

On a machine with access to the Python source, `python3
scripts/zsentinel_background_priority.py --preview` prints a single JSON
snapshot with root-disk/swap/memory/load observations, conservative mode,
recommended quota/weight and `"applied": false`. When a user systemd bus
is available, the preview additionally makes a **read-only** `systemctl
--user show` call for the current coach `unit_active`,
`current_quota_percent`, `current_weight` and
`recommendation_currently_applied`. Without a user bus it displays
`unit_observed=false` and null current values, never inventing an active
setting. It makes no service change or state-file write and does not require
Ubuntu UID 1000. The bare command remains restricted to that UID and only
changes the existing dedicated user unit. A preview's observed current
values **do not prove** this PR source was installed, nor does preview
validate runner recovery.

## Operator installation and release boundaries

Code files:
- `scripts/zsentinel_background_priority.py`
- `ops/systemd/user/zcloud-background-priority.service`
- `ops/systemd/user/zcloud-background-priority.timer`

On the targeted Ubuntu VPS user session, only an authorized runtime owner
may copy the reviewed Python script to
`/home/ubuntu/.local/bin/zcloud-background-priority.py` and the two unit
files to `~/.config/systemd/user/`. The runtime owner then reloads their
**user** manager, enables/starts the timer and invokes the oneshot service
once. There is no privileged install path or unattended production rollout
from this PR. Preserve the existing coach's always-restart policy, 3 GiB
memory maximum, Nice=10, disk-space reservation and model promotion gates.
Do not start duplicate sidecars.

This implementation adds conservative disk/swap/load admission to a
previously installed independent user-sidecar, but **source being committed
does not prove the VPS's installed copy matches this branch**. Installing
these changes requires ordinary protected-main, review, exact-head CI and
release-owner authorization. Self-hosted runner incident #1326, Actions
billing issue #1523, and project claim ownership remain separate.

The FTMO autonomous systemd service is root-managed and outside this
sidecar's authority. Its own owner must resolve the reported inaccessible
`/home/ubuntu` paths through an authorized privileged release; no blanket
permissions or zCloud SQLite write access should be granted to work around it.
