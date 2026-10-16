# Seven-writer shared-lock source inventory — 15 October 2026

This is a **read-only** source inspector for zCloud issue [#1447](https://github.com/Zennay/zCloud/issues/1447), not another owner of the existing production workflows. It does not close #1447, dispatch Actions, recover listeners, or touch the VPS.

## Why this exists

The canonical zCloud production deploy uses the shared non-cancelling group `zcloud-production-deploy`. Three separate **manual** VPS-changing jobs were still outside that group on 15 October 2026. A hosted PR check passing cannot prove those production writers are serialized at runtime.

Run from a checked-out source tree:

```sh
python3 scripts/zcloud_deployops_writer_lock_source_20261015_w3.py --root .
python3 -m unittest tests/test_zcloud_deployops_writer_lock_source_20261015_w3.py -v
```

The first command prints a bounded JSON result including per-job source outcomes. It intentionally exits 0 even when debt exists; use `--expect-serialized` when **explicitly** checking a candidate against the future all-seven-locked condition. That flag exits 1 if any source is not demonstrably locked, and does **not** authorize release when it exits 0.

For an **offline, source-byte comparison** against a separately retained 64-character snapshot fingerprint, run:

```sh
python3 scripts/zcloud_deployops_writer_lock_source_20261015_w3.py \\
  --root . --expect-source-snapshot-sha256 YOUR_PREVIOUSLY_SAVED_64_CHAR_SHA256
```

The CLI reports `source_snapshot_matches_expected`: true only if all seven bounded workflow sources were captured and their combined digest matches. Exit 2 means drift, incomplete source, or invalid digest input; exit 1 when `--expect-serialized` also requests a full-lock check but a known lock remains unresolved; exit 0 otherwise. **A matching fingerprint does not authenticate GitHub or approve an actual deploy**, and it cannot prove that unknown workflows do not write to the VPS. All existing expected mutations remain the responsibility of their separate workflow owners.


## Required source coverage

| Workflow | VPS-changing job | Effective lock location |
| --- | --- | --- |
| `zcloud-vps-deploy.yml` | `deploy` | global workflow (legacy canonical) |
| `zcloud-postsend-tab-preservation-live.yml` | `install` | job |
| `zcloud-safe-memory-reclaim-20261013.yml` | `reclaim` | job |
| `zcloud-v2-activate-addon-in-place.yml` | `activate` | job |
| `zcloud-worker-tab-guard.yml` | `install` | job (pending independent remediation) |
| `zcloud-no-browser-churn-hotfix.yml` | `install` | job (pending independent remediation) |
| `zcloud-emergency-addon-hot-reload.yml` | `activate` | job (pending independent remediation) |

The tool refuses missing, duplicated, dynamic or wrong lock group, `cancel-in-progress: true`, absent jobs, binary/unreadable/oversized files, symlinks and confusing repeated keys. It also verifies that an optional **workflow-level parent concurrency** cannot cancel an otherwise protected job: missing, duplicate, dynamic or `true` parent `cancel-in-progress` is denied rather than trusting the nested job lock alone. It detects global concurrency fields before or after `jobs`, refuses late root-level cancellation and ignores phantom jobs outside the real jobs mapping. Reads are directory-descriptor anchored with `O_NOFOLLOW` and `O_NONBLOCK`; a FIFO cannot hang the audit, short reads are retried, and metadata changes during reading are refused. **An additional read pass across all seven files** now detects source changes or failures between file inspections: the affected writer becomes `unstable_source_snapshot`, its source digest is withheld, `source_lock_complete=false` and the aggregate snapshot hash is null. These two equal reads are **not a filesystem-atomic snapshot**; an ABA modification between reads could still escape detection. The check never makes a mutable checkout authoritative. The focused hosted test suite now includes **79 adversarial cases** (including nested-parent lock cases). All seven known workflows must preserve a consistent runner topology: the named live mutation job has exactly one `runs-on: [self-hosted, zcloud, vps]` selector, while every other job in those workflows remains hosted on `ubuntu-latest`. Newly added VPS jobs, runtime expressions, missing/duplicate selectors or companion job runner drift fail closed even if the original writer lock remains correct. For job-level writers, an optional workflow-level parent group must also be present, cannot be identical to the protected production group, and must have a provably disjoint fixed prefix when using a dynamic expression. This prevents a second competing parent lock or unverifiable runtime alias from being counted as source-serialized. **GitHub Actions concurrency group matching is case-insensitive** (see [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)): the inspector normalizes parent group case and YAML trailing whitespace before comparing, and refuses ambiguous inline-comment, quoted, folded, tagged or aliased group values. Those YAML constructs can render as the same lock name despite appearing different in a narrow line-oriented source scan. The canonical workflow-level deploy lock also explicitly refuses nested live-job concurrency, which could introduce a conflicting lock or cancel-in-progress behavior. Quoted/escaped YAML mapping keys, explicit or merged mapping forms and multi-document YAML are rejected as unsupported rather than inferred safe. It examines the known static workflow/job paths; it is **not a full YAML parser**, full repository writer discovery, or runtime proof. Unlisted workflows, GitHub job scheduling, simultaneous active attempts, deployed SHA, listener liveness and production approval remain unverified. GitHub Actions' `cancel-in-progress: false` protects a **running** writer in the group, but its default pending-slot policy can replace a **previously pending** job. See [GitHub's concurrency reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency). Do not confuse this source report with guaranteed preservation or FIFO execution of every pending run. A green source inventory cannot replace independent review.

## Serialized remediation / runtime acceptance remains separate

Owners of the three manual workflow files should add **job-level** `concurrency.group: zcloud-production-deploy`, `cancel-in-progress: false`, keeping hosted PR source checks outside the production lock; recheck the exact main SHA and manual dispatch identity, independently review and only then conduct a serialized, explicitly approved runtime acceptance. No autonomous production dispatch follows from this source report. Related gates: #580/PWQ-41, #1089, #1326, #1458 and #1447.

### Readable hosted evidence

The hosted GitHub Actions job prints a Dutch per-workflow table showing source-serialization state and bounded machine-readable failure reason. This is an **advisory source report** whose own PR-head scanner is not a trusted independent proof; it never dispatches live writers or changes the issue's acceptance boundary. The source SHA-256 and optional exact-expected fingerprint CLI are equality checks on a bounded file snapshot, not evidence of trusted GitHub provenance or VPS runtime state. A hosted green check does not imply that extra writer workflows elsewhere in the repository, an SSH-based mutation from a GitHub-hosted job, or current runtime lock behavior were exhaustively ruled out.
