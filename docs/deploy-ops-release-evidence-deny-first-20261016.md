# zCloud deploy-ops — deny-first release evidence receipt (offline contract)

This contract is an **offline, read-only diagnostic**. It does not query GitHub, validate the honesty of an evidence producer, acquire the production lock, grant approval or deploy anything. Its strongest possible verdict is **operational_gates_observed=true**; **production_mutation_authorized is always false**. Do not use a passing receipt as live release admission.

## Why this matters

Hosted PR CI can be green while the actual VPS listener remains unassigned and canonical main is unprotected. The same workflow may also be missing the seven-way non-cancelling writer serialization and explicit owner release window. This small consumer refuses to compress those distinct prerequisites into one misleading "green" flag.

## Machine contract

Run only against a locally generated, fresh, bounded JSON receipt:

    python3 scripts/zcloud_release_gate_receipt_20261016.py --input /path/to/release-receipt.json

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

Every nested object must contain exactly its documented keys. No raw tokens, arbitrary metadata, secrets or HTTP payloads should be included. The parser rejects duplicate keys, nonfinite values, unknown fields, oversized inputs and symlinked receipt files. The command prints only stable reason codes (not source values).

## Operational meaning and integration boundary

A machine-generated receipt **cannot establish branch protection, genuine independent review, an assigned live VPS runner or deploy authorization on its own**; each source remains an independent owner/system responsibility. In particular, falsified positive fields still yield only an untrusted offline claim. Before any actual release, the serialized production operator must independently re-fetch GitHub branch-protection, exact SHA, CI, reviewer decision, runner assignment, all writer locks, owner approval and existing production-status/rollback gates.

Current open handoffs: [main protection #922](https://github.com/Zennay/zCloud/issues/922), [listener #1326](https://github.com/Zennay/zCloud/issues/1326), [writer locks #1447](https://github.com/Zennay/zCloud/issues/1447), [production admission #580](https://github.com/Zennay/zCloud/issues/580). None are changed or declared closed by this tool. No privileged action or live workflow was added.

The validator is intentionally a **standalone future integration seam**, not another writer inside #580/#1089. Tested in GitHub-hosted regression via the standard project test suite; no self-hosted execution is necessary.
