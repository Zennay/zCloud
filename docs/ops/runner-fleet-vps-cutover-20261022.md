# 14 GitHub Actions runners — VPS cutover (22 October 2026)

## Confirmed environment

- **Source:** `vps-bb300bba`, zSSH connected; 14 installed `actions.runner.*` systemd unit files, **8 enabled / 6 disabled** when inspected.
- **Destination:** `VM-7eb5ee16-2c71-440d-b7fd-8095d204e930` (`95.211.40.88`). All 14 copied unit files are **disabled**; do **not** start them because they reference old registration credentials.
- Destination has an authenticated SSH staging entry point as `vps-migrate`; that identity does **not** establish that it may install systemd units as `ubuntu`/root.
- Source `gh` authenticated as `Zennay` with repo/workflow scopes and can read repository Actions runner metadata.
- Source had live `Runner.Worker` processes in zCloud and Supa during inspection. **Do not interrupt them.**
- Secret-free repository/name/source-path manifest is staged at `/home/vps-migrate/zcloud-fleet-stage/github-runners-14-inventory-20261022.json` on destination. Its `custom_labels` are placeholders: the migration tool queries the *actual* source runner labels from GitHub on the cutover date.

## 14 exact repository assignments

| Repository | Old runner registration |
| --- | --- |
| Zennay/Discordbot | vps-bb300bba |
| Zennay/Ftmo | vps-bb300bba-ftmo |
| Zennay/Haxlab | vps-bb300bba-haxlab |
| Zennay/Lightup | vps-bb300bba-lightup |
| Zennay/RaiseAI | vps-bb300bba |
| Zennay/Supa | vps-bb300bba-supa |
| Zennay/Woonpoints | vps-bb300bba-pool-Woonpoints |
| Zennay/zCloud | zcloud-vps-1 |
| Zennay/zSSH | zssh-vps-1 |
| Zennay/fuckball | vps-bb300bba-pool-fuckball |
| Zennay/ikea-enhanced-app | vps-bb300bba-pool-ikea-enhanced-app |
| Zennay/Mixlab | vps-bb300bba-pool-Mixlab |
| Zennay/Tuddy | vps-bb300bba-pool-Tuddy |
| Zennay/zGuard | vps-bb300bba-pool-zGuard |

## Sequenced cutover — per runner

1. Inspect old source `systemctl status actions.runner.<exact>.service`, `Runner.Listener` / `Runner.Worker`, existing live jobs, and GitHub Actions runner API (online/busy). If any worker is live, wait for its completion; do not force-cancel.
2. After a runner is idle, on the **source**, stop and disable its **exact** systemd unit, retaining the source registration until the replacement is online and independently verified. Confirm no old listener/worker remains for that runner and GitHub reports it offline and not busy. Old jobs must not be requeued blindly.
3. On the **destination**, with an approved root/admin session and authenticated `gh`, inspect the preflight inventory before a write:
   ```bash
   python3 ops/migrate-14-github-runners-20261022.py --action inventory
   ```
   Provide `GH_TOKEN` to the process securely (never in shell history, command-line literals or logs). Do not transfer the old `.runner`, `.credentials` or private SSH keys.
4. Migrate **one confirmed drained runner at a time**, for example:
   ```bash
   python3 ops/migrate-14-github-runners-20261022.py --action migrate --repo Discordbot --old-services-disabled
   ```
   The tool demands the exact destination hostname, root privileges, GitHub token access, the old registration **offline + not busy**, its real custom labels, and restored runner binaries. Failure and timeout output for the `config.sh` registration step is redacted, including when `runuser` wraps the command, so a transient registration token is not echoed through the operator HOLD message. It copies binaries into a **new isolated directory**, excludes identity/secrets/workspaces, obtains a fresh scoped registration token, and creates a unique destination runner name without `--replace`. It installs/starts a new systemd runner unit under `ubuntu`, then confirms its GitHub state is online with the required labels. If any gate fails it stops on that repo and does not disable another old source runner.
5. Once all 14 registered runners are online, verify in each repository at `Settings → Actions → Runners`. Use an **existing harmless workflow** with matching exact labels, or wait for an already queued legitimate job to start. Do **not** dispatch deployment/trading/scanning workflows just to prove routing. Confirm positive `runner_id`, `runner_name=vm-7eb5ee16-<repo>`, job run URL and terminal status.
6. Run `python3 ops/migrate-14-github-runners-20261022.py --action verify-jobs` to collect recent assignment receipts; this requires GitHub history to contain actual matching jobs. A runner merely being online is **not** a job-routing proof.
7. Only after confirmed new activity may the old GitHub registration be deleted from that repository and the old unit retired. Keep a time-stamped operator record for each repository. If a replacement fails, do **not** run the same registration on both hosts; investigate or roll back deliberately while ensuring only one listener is ever live per registration.

## Gates not yet satisfied in this chat

- The available zSSH plugin operates against the **old VPS**, not the new host.
- New-host access was demonstrated only via nonprivileged `vps-migrate` staging; approved administrative privileges for changing the 14 new runner-services were **not** established.
- The new runner binaries, registration-token permissions and exact live labels still need destination preflight.
- Source zCloud and Supa had active jobs at inspection, so an all-at-once source shutdown was unsafe.
- No new runner has yet been registered or job-assignment-proven by this migration.

**Release acceptance:** 14/14 fresh registrations; all correct per-repository custom labels and GitHub online status; each new `systemd` unit active; no source/destination duplicate identity; source services retired; genuine positive GitHub runner IDs from assigned jobs. Track separately from zCloud production deploy readiness.
