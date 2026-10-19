# zCloud Actions listener recovery — safe resource and identity admission

**Source carrier:** [zCloud PR #1317](https://github.com/Zennay/zCloud/pull/1317). P0 incident: [#1326](https://github.com/Zennay/zCloud/issues/1326). This describes an explicitly authorized **systemd system-service** listener start only; it is not a general recovery daemon, worker queue drain, browser restart, SSH workaround or production release authority.

## What changed

The existing `scripts/zcloud_idle_actions_listener_recovery.py` now:

1. Reads actual kernel `MemAvailable`, `SwapTotal`, and `SwapFree` from `/proc/meminfo`. Missing, duplicated, malformed or impossible readings fail closed.
2. Requires at least **3072 MiB MemAvailable**, plus **512 MiB SwapFree** on hosts configured with at least 512 MiB swap. On no-/low-swap hosts it requires **4096 MiB MemAvailable** instead. It never invokes swapoff, page-cache clearing, process killing or memory reclamation.
3. Rechecks resource admission immediately before a permitted service start, including after `reset-failed`. If headroom falls below the floor, **do not start a listener**. If a reset already happened, the receipt records that prior mutation instead of claiming nothing happened.
4. Matches the **exact installed runner root** and systemd system unit, rather than treating all `Runner.Listener` processes running under `ubuntu` as the same GitHub runner. The zCloud root is `/home/ubuntu/actions-runner-zcloud/` and FTMO root is `/opt/ftmo-runner/`; systemd `User` and `ExecStart` must match their expected canonical values. A wrong unit path/user denies admission.
5. Detects target-root processes **regardless of their current Unix account**. This preserves protection against an orphaned FTMO worker running under a different account, without treating legitimate HaxLab/ELMO runners as zCloud-owned. Relative/ambiguous runner commands under the target owner still fail closed.
6. Defaults to `--lane zcloud`. FTMO service admission requires the operator to select `--lane ftmo` or `--lane both` explicitly. No existing active worker is interrupted.
7. Keeps `--apply` mandatory for a system-service **start**; dry-run only observes. No unguarded restart, stop, queue write, GitHub dispatch, or browser operation is introduced.

## Current connected VPS observation, 19 October 2026

Via the authorized **read-only zSSH** management channel:

- `systemctl show actions.runner.Zennay-zCloud.zcloud-vps-1.service` returned `LoadState=loaded`, `ActiveState=failed`, `SubState=failed`, `Result=oom-kill`.
- `free -m` returned **3606 MiB available RAM** but **0 MiB free of 4095 MiB configured swap**.
- Other listeners were observed on the same host, including `/home/ubuntu/actions-runner-elmo/bin/Runner.Listener`, `/opt/actions-runner-haxlab/bin/Runner.Listener` and `/opt/ftmo-runner/bin/Runner.Listener`. The latter was seen under user `nobody`, while its registered system unit declared `User=ftmo-runner` and `ActiveState=inactive`; this is why username-only process counting is both too broad and unsafe.
- The zCloud system unit declares `User=ubuntu` and `ExecStart=/home/ubuntu/actions-runner-zcloud/runsvc.sh`.

**Interpretation:** the current zCloud listener **must not be started** while its swap headroom is exhausted. No attempted restart, swap clearing, service mutation or daemon reload was made. This source change does not claim that the VPS has recovered.

## Safe operator flow

On the already authorized permanent VPS, with its normal system-service permissions:

```sh
python3 scripts/zcloud_idle_actions_listener_recovery.py --lane zcloud
# Observe again only after independent owner-approved memory-headroom recovery.
# Apply only with owner authorization, no active worker, exact verified source,
# eligible resource admission, and the existing serialized production gates.
python3 scripts/zcloud_idle_actions_listener_recovery.py --lane zcloud --apply
```

An output of `deny_configured_swap_exhausted`, `deny_insufficient_ram_headroom`, `deny_residual_runner_processes`, `error` or any unknown service state means **no further automatic work**. Resource remediation and owner authorization are independent; do not restart unrelated Firefox, FTMO, HaxLab, or ELMO workloads, and do not treat a repaired unit as proof that an actual GitHub job acquired a runner ID, completed, or that zCloud deployed successfully.

**Release gates:** the PR is still draft, and original CI, independent owner review, branch protection, main release and exact VPS acceptance must be satisfied before deployment. These source edits add no tests or QA suites and have not executed a live `--apply` operation.
