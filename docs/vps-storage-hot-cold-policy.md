# zCloud VPS hot/cold storage policy (20 October 2026)

## Decision
The encrypted external **VPS-Archief** is the default *cold* tier for data no longer required for current execution, not the runtime filesystem. The VPS has a 96 GiB local root volume; the crypt-backed Google Drive remote `vpsarchive:zcloud-vps` exposes approximately 5 TiB. The current FUSE browse mount `/home/ubuntu/VPS-Archief` is deliberately **read-only**. Write operations, when separately authorized, use the encrypted `rclone` remote, not that browse mount.

## Stay on local SSD
- Active zCloud orchestration, secrets and connection settings; repositories checked out for execution, modified/uncommitted worktrees, workers and self-hosted runners.
- SQLite/postgres databases, open files, browser profiles, action runner work and current logs.
- HaxLab archive/source manifests, raw replays, training shards, current and frozen holdouts, analysis queues, model generations being written, champion pointers and active ML pipelines.
- Fuckball current and previous two cycle directories, latest model/weights, training status/lock and **50 most recent cycle reports**.
- FTMO live/paper state, seals, broker data, risk checks and current research inputs.
- Assets required by an actively served game/app.

Do not point hot random-access reads at the FUSE cloud mount: latency, missing connectivity and inconsistent locking can break training and live gameplay.

## Eligible cold classes
1. Immutable dated backups, older APKs, exports and historical research snapshots no longer referenced by a service.
2. Closed GitHub Actions `Runner_*.log`, `Worker_*.log` and `SelfUpdate_*.log` files older than three days. Inventory is available in `scripts/vps_storage_audit.py`.
3. Fuckball completed `cycle-N-report.json` files older than three days and at least 50 cycles behind current status; the live model and training reports for recent cycles stay local.
4. Independently checked old standalone Git clones, with no worktrees, open file descriptors or active processes (existing `.drive-archive` workflow). Do not blanket-move `zennay-cloud/repos`, `worktrees` or all `/home/ubuntu`.

The previously verified `fuckball-human-shards-20261015.tar.zst` is an *archive copy*. The original training shards remain local because dataset manifests reference their actual paths. Updating those paths requires a separate data-lineage migration with an explicit restore strategy.

## Transfer sequence (no loss / no hidden production change)
1. Produce read-only candidate inventory and check disk, active process references and cloud health.
2. Make a versioned archive under `vpsarchive:zcloud-vps/cold/` using the preinstalled encrypted rclone remote; throttle I/O and concurrency so HaxLab and Fuckball remain responsive. Avoid exporting credentials or active sessions.
3. Download/stream remote bytes back and compare SHA-256 (or `rclone check --download` on immutable inputs), then recheck local inode, size and mtime; record a manifest and restore command.
4. Only **after independent encryption-key recovery is confirmed** and the existing explicit allow-prune gate is met may a separate cleanup job remove a verified, old, unreferenced local source. No generic `rclone move`, `sync --delete`, blanket `rm -rf`, or automatic model/DB relocation.
5. Before declaring completion, record **actually reclaimed free space**, remote archive paths, verified object counts, unchanged trainer state and restored ability to create new Fuckball cycles.

The existing user timers `zcloud-drive-archive.timer` and `zcloud-drive-cleanup.timer` already provide scheduled execution; do not start a second concurrent uploader or bypass the existing approval marker. Continue the existing archive workflow instead of creating a competing rclone daemon.

## Current constrained incident
On 20 October, local root had only about **6.3 GiB free** and Fuckball remained at **707 completed cycles, paused with low disk space**. The training service reserves **10 GiB**. The read-only candidate auditor can identify low-risk old logs/reports, but an archive copy on its own does not free local disk. Avoid a claim of resumed training until verified cleanup increases root space and the status reports progress. A previous crypt-remote directory creation succeeded, but attempted file transfer was denied by the connector safety control; **do not bypass it via an indirect job**.

## Inventory
```bash
python3 scripts/vps_storage_audit.py --older-than-days 3 --limit 20
```

This command is strictly read-only and is safe during active training. It does not copy or prune files. Work in this branch is a storage discovery/policy integration, not a completed offsite migration or a training restart.


## 20 October · measured training free-space reserve instead of optimistic archive estimates

The existing read-only `scripts/vps_storage_audit.py` now exposes a `disk_pressure` section next to candidate inventory and `archive_mount`. The report measures **actual** local free bytes against the existing 10 GiB training headroom threshold. It separately computes an *optimistic upper bound* for old candidate bytes: even if every assessed candidate were independently verified and eventually authorized for deletion, would the total be enough to close the reserve gap? The answer must never be reported as reclaimed bytes.

All outputs explicitly declare `source_deletion_authorized=false`, `remote_copies_verified=false`, `bytes_reclaimed=0`, `training_restart_authorized=false`. The mounted `VPS-Archief` path is detected read-only through the kernel mount table; `mounted_write_unverified` still does **not** grant remote-write permission. Actual encrypted transfers must use the existing approved uploader and its remote full-byte/restore proofs.

**Direct live source inspection (read-only, no installed script, no QA suite):** connected zSSH executed the exact immutable source `8b1a02d` in memory against the VPS. Observed root available **9,914,683,392 bytes**, which is **822,734,848 bytes below** the 10 GiB reserve. This bounded source sample found **381** older diagnostic/training-report candidates totalling **833,723 bytes**. Even hypothetical verified pruning of *all* of those would leave a gap of **821,901,125 bytes**. Mount: `fuse.rclone`, `mounted_read_only`. **No bytes were moved/reclaimed and Fuckball training was not restarted.** The eligible candidate count and amount may legitimately differ from older snapshots, since source files and access change; the prior unverified ~1.6 GiB statement must not be treated as current reclaimable capacity.

This makes the next real storage choice explicit: a separately authorized search for other *completed, unreferenced, recoverable* cold objects or a larger local disk, without gambling on active models, live databases, dirty worktrees, trainer inputs or a read-only archive browse mount. Existing uploader/prune permissions, remote crypt recovery proof, and service ownership remain unchanged.


## Optional old release file review (not a cleanup authorization)

For **one-time/manual operator assessment**, the read-only inventory also supports:

```bash
python3 scripts/vps_storage_audit.py --older-than-days 3 --include-release-reviews --limit 30
```

The additional scanner is restricted to aged standalone `/home/ubuntu/RaiseAI-v*.apk` and `/home/ubuntu/Downloads/*.apk` exports, a **minimum 14-day age** regardless of the ordinary three-day log horizon. It excludes active Gradle trees, signed-key material, browser profiles, datasets, models and APK installation files in current build directories. It marks each row `release_superseded_verified=false` and `can_remove_automatically=false`; merely old doesn't establish unused.

On the live VPS, exact source `0df732b` read-only inspection identified **two** possible manual APK review objects totalling **178,153,114 bytes**. Existing three-day old diagnostics/reports remained **833,723 bytes**. Those figures are **possible review surface**, not crypt-backed remote copies or reclaimable space. Even if both old APK files were independently proven superseded, encrypted-copied, remote-verified, and explicitly cleared by their release owners, this alone would not cover the measured ~823 MB gap to the 10 GiB free-space reserve.

Do not rename/delete these installation artifacts, change the browse mount's access mode, or let the storage inventory silently invoke a transfer. Protect app release/signing owners, backup provenance and the currently running Fuckball/HaxLab pipelines. This code adds **discovery**, not automatic cleanup, storage migration or restart authority.


## Hot-path operator view and actual growth tracking (20 October)

For the existing storage issue, the old cold-candidate list is not enough: current logs and stale APK exports are far smaller than the remaining 10 GiB training headroom gap, while live runner/Android/FTMO caches are several GiB. The improved existing audit supports a **single human-readable read-only hot-path inventory**:

```bash
python3 scripts/vps_storage_audit.py --hotspot-inventory --format text
```

This inspects **only** five fixed known directories using filesystem-bounded `du -sx` with a four-second deadline per directory. It lists the size and responsible owner of the zCloud Actions worktree, user cache, Gradle cache, RaiseAI build directory, and FTMO runner installation. All are `hot_or_owner_approval_required` with `can_remove_automatically=false` and `bytes_proven_reclaimable=0`. Unknown, inaccessible or timed-out measurements are explicit, never silently zero. The script does not walk the 5 TiB FUSE archive, retrieve credentials, or delete data.

A connected zSSH direct-source inspection of commit [47272422](https://github.com/Zennay/zCloud/commit/47272422e81563d757d1e22819fefea0d7185fcc) observed a total of approximately **8.37 GiB** across the five known hot locations. This total is a *disk footprint*, not a cleanup entitlement; particularly the roughly 2.81 GiB FTMO runner and 2.02 GiB zCloud Actions worktree should remain entirely untouched unless the actual respective owner independently proves an old/unused subpath.

### Compare two observations without claiming reclaimed space

The `--previous-report` option reads only a bounded, schema-checked operator-owned JSON report. It calculates actual change in available local bytes and same-category path size differences; it does not infer that free-space gains represent verified deleted files. The prior report must include an UTC observation timestamp and retain the exact source schema. Do **not** use a shared public webroot for reports; operator storage should be private:

```bash
python3 scripts/vps_storage_audit.py --limit 20 --hotspot-inventory > /private/path/first.json
# At a later owner-approved observation window:
python3 scripts/vps_storage_audit.py --limit 20 --hotspot-inventory \
  --previous-report /private/path/first.json --format text
```

The comparison refuses symlinks, oversized/malformed snapshots, impossible clocks, invalid size fields, duplicated categories and non-UTC timestamps. Missing hot-path categories cannot be assumed to have zero bytes. **Neither report writes an archive, prunes a runner or authorizes training restart.** Mandatory encrypted remote integrity and explicit deletion review continue to apply.

The 20 October next-source inspection at [7c0a64c](https://github.com/Zennay/zCloud/commit/7c0a64c756f251dbed2a13905f9471e8f8ddbb68) still found the 10 GiB reserve unmet, with approximately **864 MB** of shortfall at that later instant. This is volatile operational observation, not persistent clearance.


## Standalone responsive storage owner report

Existing PR source now also renders `--format html` with no service installation, JS or external CDN:

```bash
python3 scripts/vps_storage_audit.py --hotspot-inventory --format html > /private/path/storage-review.html
```

The HTML report has three directly usable views: actual free bytes versus the existing 10 GiB training reserve, bounded large-hot-path sizes with explicit owners and **not-reclaimable-by-default** classification, and the current external archive mount's read-only/unverified status. Passing `--previous-report /private/path/first.json` adds an actual size-delta comparison; absent prior data cannot be turned into an invented zero. Paths, active model pointers, archive credentials, source APK filenames and browser/queue data are omitted from the public-facing presentation. Store the generated HTML only in a private operator location.

Connected zSSH direct inspection of the exact immutable source [`6d80e65`](https://github.com/Zennay/zCloud/commit/6d80e65bb19e9219fa2179270b0266f67ac6ecdd) confirmed the current five hot categories render, HTML is self-contained/mobile viewport aware, and the reserve still remains below threshold. The measured local deficit was **962,039,808 bytes** at that later time, demonstrating why scheduled storage comparisons matter. This inspection never wrote a report, copied data, cleared caches, stopped workers or installed a production handler.

Source belongs to **open PR #1569**. Pending exact-HEAD GitHub CI, independent review, branch protection and owned storage/release approvals remain intact. A source commit or preview is not a merged main feature or a live deployment.


## Review confidence and bounded candidate count (20 October, continuation)

The inventory previously called three-day-old runner logs `completed-runner-diagnostic`. That was an unsafe conclusion: an old modified timestamp is **not** a proof that a long-running Runner.Listener closed the file descriptor. Existing code now reports `aged-runner-log-owner-review` and `aged-training-report-owner-review` instead, with `no_open_handle_verified=false` and `archive_transfer_approved=false`. Optional older APK rows continue to require independent verification that the release is superseded. All candidate rows retain `can_remove_automatically=false`.

The HTML and text operator reports now show per-class **counts and size**, state that the files require owner/open-handle verification, and disclose the exact number of individual examples omitted after the `--limit` threshold. The JSON separately provides `total_candidate_count`, `examples_limit`, `examples_omitted` and `archive_transfer_attempted=false`. Hence a bounded 12-row operator example page must not look like all local inventory has been examined or approved. No renderer embeds file paths, private titles or remote encryption configuration.

The connected zSSH direct-source read-only inspection of [`57a2cb4`](https://github.com/Zennay/zCloud/commit/57a2cb4ca6be9cb5e6c51f99f56a5f8eb7b7befc) observed **385** older training report files requiring owner review, not 385 completed, safe-to-move files. The subsequent [`e7c528f`](https://github.com/Zennay/zCloud/commit/e7c528fa9a4e2c370315ede6d5b81abcc76f6ce9) updates both privacy-safe HTML and text descriptions. Neither source invocation copied data, modified log handles, wrote cleanup manifests to production, restarted training or executed QA suites.

This is still source in open PR #1569, not shipped on production main; only the existing owner's encrypted remote verification, access rights and separate file-retention approval can authorize later data movement and deletion.


## Bounded live inventory under heavy VPS load (20 October continuation)

Cold-candidate scanning is now **streaming**, not a Python tuple of every eligible historical log/report. The source iterates over runner diagnostics, completed-age training-report *review* entries, and optional older APK review objects lazily and retains at most the requested number of example rows. It stops when the **8-second** scan budget or **10,000 observed-candidate** ceiling is reached. The full list is never pre-materialized, and the existing five hot-path `du -sx` scans retain their separate per-path deadlines.

The operator view now exposes `inventory_complete`, `scan_limit_reason`, `scan_seconds_budget` and `scan_candidate_limit`. On any budget-exhausted scan it warns that candidate counts and byte totals are only *observed lower bounds*, not a complete archiving inventory. Neither reaching an input row limit nor a runner log being old proves it is safe to archive or delete. The runner directory itself must not be a symlink, even when the contained `_diag` directory appears safe.

Connected zSSH directly inspected and ran the immutable read-only source at [`460d2e5`](https://github.com/Zennay/zCloud/commit/460d2e5f987a46c0c1c2032c4c9a56a434bcb797), without installing it. At that instant the scanner observed **386** age-qualified entries, of which **12** private examples were displayed and **374** explicitly marked omitted; `inventory_complete=true`, `archive_transfer_attempted=false`, `bytes_reclaimed=0`. The self-contained HTML review section was present and no external script was loaded. This was a direct functional source inspection, not a new QA suite or production deploy.

No closed-file verification, crypt upload, checksum, cleanup, claim change or trainer restart was performed. The existing owner-gated transfer/retention sequence is still required, and this PR still needs exact-head GitHub CI and independent approval before main merge.


## Runtime open-file warnings before archival assessment

Use the optional **positive-only** live handle observation only when a specific owner is evaluating a bounded set of aged files:

```bash
python3 scripts/vps_storage_audit.py --older-than-days 3 \
  --limit 35 --open-handle-hints --format text
```

The scanner reads candidate filesystem inode identities and looks for matching descriptors in a bounded window of `/proc/*/fd` (up to 350 processes, 5000 descriptors and 2 seconds). If a match is observed, `open_handle_observed=true` flags that **displayed file** as in use, and it must not be considered for archival while open. No file descriptor is closed, no process is signalled and no private PID or descriptor target is included in the output.

**Critical boundary:** no observed match does NOT mean a file is closed. Restricted `/proc` access, process churn, path races and cross-host storage make a negative observation untrustworthy. The result always has `no_open_handles_proven=false`; candidate rows now always have `no_open_handle_verified=false`, including old runner diagnostics, training reports and APK exports. Even a positive open-file observation does not authorize writes, nor does the absence of one. The mandatory independent archive owner, encrypted remote checksum and separate prune approval remain intact.

Read-only live source inspection on 20 October of exact [`aa1bd2e`](https://github.com/Zennay/zCloud/commit/aa1bd2eae6d7e3221becb8ef9a7ad1eb76f0246a) sampled 35 old training reports and approximately 4291 visible FDs across the first 350 process entries. It found no positive candidate matches **but marked the observation incomplete** and all 35 files as unverified. No closed-file proof, transfer, cleanup or trainer restart occurred. The actual product remains on the existing open PR until required CI, independent review and protected-main merge.

