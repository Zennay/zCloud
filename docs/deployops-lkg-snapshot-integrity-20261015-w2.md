# LKG snapshot integrity probe — deploy-ops 2026-10-15 (W2)

## Waarom

The existing last-known-good (LKG) snapshot hashes are produced by a tree walk that excludes symlinks. As a result, checking the file hash dictionary alone does **not** prove that the snapshot contains only ordinary files. A symlink substituted inside a snapshot could escape the expected managed tree if an operator blindly restores it. A malformed snapshot pointer can also select unexpected paths unless its identifier is constrained. This is a **source-level trust-boundary finding**, not evidence that an attack happened on the VPS.

The live `scripts/zcloud_recovery.py` and `tests/test_zcloud_recovery.py` are exclusively owned by [PR #595](https://github.com/Zennay/zCloud/pull/595). This PR does not edit those files, the recovery workflow, active services or persistent state.

## Wat nu uitvoerbaar is

```bash
python3 scripts/zcloud_lkg_snapshot_integrity_probe_20261015_w2.py \
  --state-dir "$HOME/.local/state/zcloud/recovery"
```

Exit 0 + `ok:true` means that **the selected snapshot**, as observed, had an exact hash-matched ordinary-file tree. Exit 2 + a fixed reason means **deny/manual investigation**. Both outcomes declare `mutation_performed:false`, `authorizes_rollback:false`, `authorizes_deploy:false`. No secrets, paths or file content appear in stdout. A successful probe is not live-health evidence and **never authorizes rollback, deploy or service changes**.

The probe performs a bounded 2 MiB JSON read, an enumeration capped at 150,000 paths, a 50,000-file inventory and a 512 MiB content read, verifies exact file hashes and requires a hashed `server.py` entrypoint; rejects symlinks, hardlinks, special files, absolute/traversal names, unmanaged source roots, unexpected files, duplicate JSON keys, non-finite JSON and path escape in the pointer. It never follows `pointer.manifest`. Input snapshots are local and must be trusted to remain stable while scanning: no concurrent-writer atomicity guarantee is claimed, and a separate authorized release control is still required.

## Validatie en eigenaarschap

- Synthetic stdlib tests cover valid evidence, hash drift, path traversal, symlink insertion, duplicate pointers, manifest drift, bounds, read-only CLI and fixed-denial output.
- `.github/workflows/zcloud-lkg-integrity-probe-20261015-w2.yml` runs **GitHub-hosted only**, PR-only, with `contents: read`, immutable checkout, `persist-credentials:false`, same-repository / owner / main checks and no GitHub API/VPS mutation.
- A passing proof does not authenticate the manifest author: a forged snapshot and matching forged manifest are outside this checksum-only integrity contract. No new self-hosted runner job, dispatch, SQLite transaction, browser change or artifact upload.
- Integration into the live rollback path is **separately owned** by #595 and remains intentionally unimplemented here. The preflight should be reviewed by that owner before any operational wiring.
- Release gating remains #580 / PWQ-41 / #1089, branch protection #922, runner #1326 and writer locks #1447. A green hosted check alone cannot clear them.

## Cross-field inventory proof (second implementation wave)

The original `format_version=1` recovery manifest contains `managed_paths`, `persistent_paths_never_restored`, `present_paths` and `config_hashes` in addition to `hashes`. A checksum-only check could have accepted a contradictory set of these fields. The offline preflight now validates these as one coherent inventory:

- Every declared managed root must be on the known restore allowlist, and the entrypoint must be declared and hashed.
- Persistent exclusions must match the recovery contract exactly. Omissions, duplicates and unexpected entries are refused rather than silently ignored.
- `present_paths` must be unique, known, contained within `managed_paths`, and equal to the actual direct children of the snapshot `files/` directory, including empty managed directories.
- Each configured source file `projects.json`, `project-layout.json`, `resource-policy.json`, `project-contracts.json` must have exactly matching `config_hashes` and `hashes` entries; absent config files must not be invented in metadata.
- File roots cannot pretend to be directories and directory roots cannot pretend to be files. Parent root types are checked in addition to individual hashes.
- Pointer and manifest are re-read after verifying snapshot bytes to catch ordinary metadata drift during the scan. This is **not** a race-proof filesystem transaction: a privileged concurrent writer could still mutate contents between checks. Genuine release authority remains elsewhere.

A missing or contradictory field produces a fixed denial code. These stricter metadata semantics may reject unusual historical snapshots; they do not automatically rewrite, delete, migrate or repair old snapshots. Operators should investigate and use the separately approved recovery process; do not override this guard based on a passing hosted CI check alone.

## Nofollow directory access and FIFO race

A file-hash read using ordinary `Path.open()` can follow an intermediate directory symlink if another process swaps that directory between inventory and file read. The probe now opens each snapshot path component relative to an already-open parent directory using `dir_fd`, `O_DIRECTORY` and `O_NOFOLLOW`; file reads also enforce `O_NOFOLLOW` and `O_NONBLOCK`. This rejects a forged intermediate symlink and prevents a FIFO substitution from hanging an otherwise bounded JSON read. An adversarial test performs an actual parent-directory swap immediately before the file-reader call; a separate test replaces the pointer with a FIFO after the initial stat. Both must fail closed.

This safety measure addresses following the wrong path during a scan. It does **not** provide cryptographic authenticity, an exclusive filesystem lock, or a complete atomic snapshot view. Concurrent privileged changes and mount-level attacks remain outside this offline probe's guarantee.

## File identity drift during hashing

For each source file, the probe now records `fstat()` identity (device, inode, size and nanosecond modification/change timestamps) before and after hashing, then compares the final pathname's `lstat()`. Content changed while scanning or a replaced pathname produces `source_changed_during_scan` even if the bytes that were read happened to match the expected hash. Two deterministic tests mutate mtime during read and replace the inode after read. This reduces ordinary scan-time inconsistency; it is explicitly not an atomic snapshot guarantee and never grants rollback authority.

## Complete directory inventory: fail closed on unreadable subtrees

On supported Linux deployments, the scanner enumerates the snapshot tree through explicitly opened directory descriptors, `os.scandir(fd)`, `O_DIRECTORY` and `O_NOFOLLOW`; it refuses path depth over 64 and inventory sizes beyond the configured cap. This replaces `pathlib.rglob`, which can suppress traversal errors and therefore allow a partial inventory to look complete. An unreadable subtree, an injected enumeration failure or a too-deep tree is a denial, never an implicit empty directory. Targeted adversarial tests cover both cases, and no new VPS permissions or mutation operations were introduced.

## All retained snapshots — read-only operator inventory

The standalone preflight now also supports `--all-snapshots`, which verifies the selected active snapshot and every other retained snapshot without switching the active pointer:

```bash
python3 scripts/zcloud_lkg_snapshot_integrity_probe_20261015_w2.py \\
  --state-dir "$HOME/.local/state/zcloud/recovery" --all-snapshots
```

The JSON summary contains a bounded list of recognized snapshot IDs, their status, currentness, verified byte count and fixed-category failure reason. Unknown snapshot filenames are deliberately hidden (`snapshot_id:null`) and treated as a denial. At most 16 entries and 1 GiB of cumulatively verified data are admitted; more entries or bytes fail closed without truncating a purportedly complete report. The current pointer must remain unchanged and present. A clean historical snapshot is **not** a suggestion to select or restore it; each result always declares `authorizes_rollback:false` and `authorizes_deploy:false` at the report level. The inventory only reads files and performs no cleanup, pruning, restarts, deployments, rollback or alternate-pointer writes.

Additional hardening: all metadata and snapshot readers walk every ancestor via nofollow directory descriptors, so a symlink swapped into an upper directory cannot redirect reads outside the recovery state. The full managed restore-path contract is checked exactly. JSON exponent overflow and lone Unicode surrogate escapes are rejected even in otherwise ignored metadata fields.

The all-snapshot report checks the bounded snapshot-directory identity list both before and after inspection; added, deleted, or retyped entries yield `snapshot_inventory_changed` rather than an apparently complete report. An explicit aggregate 1-GiB cap refuses excessive verification work. Synthetic tests inject a new snapshot during iteration, exceed the batch byte budget and prove that unknown on-disk directory names cannot leak through either the structured response or CLI output. This still does not claim atomicity against simultaneous privileged mutation.

## Recovery source-format compatibility

The production `zcloud_recovery.capture()` short identifier is `(git_head or "working-tree")[:8]`. Therefore a legitimate no-Git LKG ID ends in `-working-` (and a collision in `-working--2`), **not** merely `-working-tree`. The probe now accepts both the generated and compatible historical spelling, validates the calendar date/time of every selected ID, and tests two actual synthetic `capture()` calls without a Git HEAD. This prevents a false denial of an otherwise valid locally produced LKG while continuing to reject malformed dates and traversal.

## Offline cross-snapshot config comparison (read-only)

The `--all-snapshots` JSON now includes an optional `config_drift_from_current` object for **verified** snapshots when the currently selected snapshot itself passes the integrity check. The comparison lists *filenames only*: `changed`, `only_in_snapshot`, `only_in_current` for the four managed configuration files. It never publishes file contents or SHA-256 values. Config differences between healthy independent snapshots are normal and **do not make an otherwise valid snapshot fail**; the report is a diagnostic hint, not a recommendation to deploy or roll back. If the current snapshot fails verification, comparison is withheld rather than inferring a healthy baseline from any historical snapshot.

Before recording a config inventory the scanner reads the selected snapshot manifest, calls the normal complete integrity preflight, then checks the manifest again for equal parsed contents. This narrows ordinary concurrent metadata churn but remains neither a filesystem lock nor a cryptographic authentication claim. The original standalone selected-snapshot CLI output stays unchanged apart from the existing non-authorizing fields.

Tests also compare probe allowlist and excluded persistent roots against the actual `zcloud_recovery` constants. A future source contract change will visibly fail hosted CI rather than silently loosening the standalone scan.

## Resource bound: charge rejected snapshots, too

The aggregate 1-GiB `--all-snapshots` content-read limit is now charged **inside the per-file read loop**, through one shared batch counter. Previously the counter was incremented only after an entire snapshot had passed integrity validation; an attacker-controlled collection of corrupt snapshots could have forced significantly more reads than the advertised aggregate limit while each invalid snapshot consumed zero of the budget. Rejected snapshots now count every byte read before their failure, and exceeding the global budget produces a terminal `batch_byte_limit` denial rather than being reported as an ordinary per-snapshot finding. This closes the source-level accounting gap without changing the single-snapshot 512-MiB limit, attempting repair, or exposing file contents.

Deterministic regression tests cover a corrupt historical file followed by a valid current one, an oversized corrupt historical file, and the exact permitted aggregate boundary. The limit covers content bytes passed to the hash reader; it is **not** a wall-clock deadline or an exhaustive bound on filesystem metadata I/O, so existing per-snapshot, JSON, path count and depth bounds remain independently required.

## Snapshot directory identity during batch scans

The batch inventory now records device and inode numbers for every retained snapshot entry at the start and end of its scan. Comparing names and `is_dir` alone was insufficient: a concurrent process could replace a snapshot directory with a new directory under exactly the same name without changing those two fields. Such a replacement now produces `snapshot_inventory_changed` even when the replacement contains the same byte content. A deterministic test renames the original snapshot, copies it back under its previous name and deletes the old directory between inventory passes. It fails closed without touching the live snapshot pointer. This is a stronger observational check, **not** a filesystem transaction or a live rollback authorization.

## Selected-snapshot inode binding

The selected-snapshot `inspect()` path now records the actual device/inode identities of both the selected snapshot directory and its `files/` directory with descriptor-based `O_NOFOLLOW` checks at entry. It reopens both at exit, and emits `snapshot_changed_during_scan` if either directory is replaced under the same pathname. This also protects single-snapshot operator checks; previously only the all-snapshots before/after directory inventory detected a same-name replacement. Synthetic adversarial tests replace the snapshot directory or the nested files directory with byte-identical copies between manifest checks and require a safe denial. This remains an observational guard, not atomic locking or a release authorization.
