# zCloud deploy-ops — deny-first release evidence receipt (offline contract)

This contract is an **offline, read-only diagnostic**. It does not query GitHub, validate the honesty of an evidence producer, acquire the production lock, grant approval or deploy anything. Its strongest possible verdict is **offline_claims_complete=true**; **production_mutation_authorized and live_sources_verified are always false**. Do not use a passing receipt as live release admission.

## Why this matters

Hosted PR CI can be green while the actual VPS listener remains unassigned and canonical main is unprotected. The same workflow may also be missing the seven-way non-cancelling writer serialization and explicit owner release window. This small consumer refuses to compress those distinct prerequisites into one misleading "green" flag.

## Machine contract

Run only against a locally generated, fresh, bounded JSON receipt:

    python3 scripts/zcloud_release_gate_receipt_20261016.py --input /path/to/release-receipt.json

For Dutch operator-facing audit text instead of JSON codes:

    python3 scripts/zcloud_release_gate_receipt_20261016.py --input /path/to/release-receipt.json --format markdown

Exit 0 means **the supplied claims satisfy this offline schema only**. Exit 2 means the receipt is missing, unsafe, malformed, stale or claims one or more missing gates. No network access or state mutation is performed.

The entire top-level object must contain exactly:

- schema: literal "zcloud-release-evidence-v1".
- candidate_sha and canonical_main_sha: identical lowercase immutable 40-character commit SHAs.
- observed_at: UTC ISO-8601 timestamp ending in Z, no more than 10 minutes old and not in the future.
- ci: head_sha, conclusion="success", and exact zCloud GitHub Actions run_url.
- review: head_sha, approved=true, independent=true.
- protection: main_sha, enforced=true, reviews_required=true.
- runner: main_sha, positive integer runner_id, labels containing self-hosted / zcloud / vps, and exact zCloud Actions job_url.
- locks: main_sha, production_writer_jobs=7, all_serialized=true.
- release: main_sha, owner_approved=true, exclusive_window=true.

Every nested object must contain exactly its documented keys. No raw tokens, arbitrary metadata, secrets or HTTP payloads should be included. The parser rejects duplicate keys, nonfinite values, unknown fields, oversized inputs and symlinked receipt files or parent directories. Both JSON failure outputs and completed-claim outputs explicitly retain `live_sources_verified=false` and `production_mutation_authorized=false`.

`--format markdown` renders a bounded Dutch table for all eight gates. Every row differentiates *claimed complete but not verified*, *missing/invalid claim*, and *not assessable due to invalid input*, with an owner/action description. It never inserts raw receipt values, tokens, arbitrary URLs or terminal escapes into the audit text. This improves operator triage, not release admission.

## Operational meaning and integration boundary

A machine-generated receipt **cannot establish branch protection, genuine independent review, an assigned live VPS runner or deploy authorization on its own**; each source remains an independent owner/system responsibility. In particular, falsified positive fields still yield only an untrusted offline claim. Before any actual release, the serialized production operator must independently re-fetch GitHub branch-protection, exact SHA, CI, reviewer decision, runner assignment, all writer locks, owner approval and existing production-status/rollback gates.

Current open handoffs: [main protection #922](https://github.com/Zennay/zCloud/issues/922), [listener #1326](https://github.com/Zennay/zCloud/issues/1326), [writer locks #1447](https://github.com/Zennay/zCloud/issues/1447), [production admission #580](https://github.com/Zennay/zCloud/issues/580). None are changed or declared closed by this tool. No privileged action or live workflow was added.

The validator is intentionally a **standalone future integration seam**, not another writer inside #580/#1089. Tested in GitHub-hosted regression via the standard project test suite; no self-hosted execution is necessary.

## Human-language audit semantics

The renderer has a permanent decision line: **GEEN PRODUCTIEVRIJGAVE**. A claimed complete gate is **not** independently verified; a source-only proof cannot lift a real runtime blockade. Owner handoffs remain explicit: branch governance #922, VPS listener #1326, all seven shared writer locks #1447, and serialized production owner #580. The offline input can be corrected immediately if stale/malformed, whereas branch protection, runner access, lock repair, and serialized release require separate authorized owner actions. Neither JSON nor Markdown outputs authorize a GitHub workflow, runner recovery, PR merge or deployment.

## File-descriptor anchored parser hardening (16 October follow-up)

The input file is now opened strictly read-only through directory file descriptors (`O_DIRECTORY|O_NOFOLLOW`) for **each path component**, followed by a regular-file-only leaf (`O_NOFOLLOW|O_NONBLOCK`). No path resolution is trusted between a precheck and its file open. A directory rename/symlink replacement cannot redirect the leaf read to an attacker-controlled JSON file: even if a parent path is swapped during opening, the already-open parent descriptor points to the original directory. Parent symlinks, final symlinks, `..` traversal, nonregular files (including FIFOs) and missing files deny with a redacted stable error. The file is read at most `MAX_BYTES + 1`, with a 64 KiB acceptance cap. No directory content or file is modified.

The JSON parser additionally rejects exponent overflow to nonfinite floats (e.g. `1e9999`), excessive recursive nesting and overlong integer literals without an uncaught traceback, while preserving specific codes for duplicate keys and NaN/Infinity. An untrusted malformed receipt still returns both `live_sources_verified=false` and `production_mutation_authorized=false`; neither positive nor negative offline outcomes can approve a live release.

Deterministic tests cover absolute/relative nested valid evidence, parent and leaf symlinks, missing path and traversal, FIFO nonblocking denial, a simulated parent-directory swap *after its directory descriptor was opened*, JSON deep recursion/large integers/nonfinite exponent and CLI deny-path redaction. This is hardening of the existing dedicated PR, not a new overlapping privileged runner workflow.
