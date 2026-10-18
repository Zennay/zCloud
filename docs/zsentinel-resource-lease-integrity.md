# Resource-lease admission integrity (zSentinel, 18 October 2026)

Fixes [#1271](https://github.com/Zennay/zCloud/issues/1271) and [#1272](https://github.com/Zennay/zCloud/issues/1272) in the **core** resource lease writer, not just the CLI.

## Operator-visible behavior

- An owner ID must be a nonblank UTF-8 string of 1–200 Python Unicode characters with no NUL or unpaired surrogate. Invalid acquire requests return `acquired: false`, `reason: invalid_owner_id`; invalid releases return `released: false`. No cleanup, insert, update or deletion occurs on this denial.
- A metadata argument must be a JSON-serializable object (or omitted). Serialized canonical JSON must be complete, finite and no more than **4000 UTF-8 bytes**. Invalid acquisition/renewal returns `acquired: false`, `reason: invalid_resource_metadata` **before** the expired-lease cleanup or any persistent write.
- Valid 200-character owner IDs are stored unchanged, queried unchanged, renewed unchanged and released unchanged. Different 201-character identities are rejected before they could collide after truncation.
- Valid metadata of exactly 4000 bytes is stored unchanged; longer or non-finite/non-JSON data is rejected. Renewal rejection preserves the previous lease and its metadata. No raw supplied metadata is echoed into response messages.
- Internal impossible post-write missing lease reads raise a deterministic runtime exception rather than returning a misleading `acquired: true, lease: null`.

## Integration and limits

This patch changes only the resource-lease admission portion of `project_runtime.py`; it does not modify the API, server, worker claims, resource capacity, resource pool allocation algorithm, queue, migrations, CI, or live SQLite. The existing draft [#1270](https://github.com/Zennay/zCloud/pull/1270) owns separate receipt freshness/pool-slot validation hunks, and open [#1545](https://github.com/Zennay/zCloud/pull/1545) owns separate receipt-provenance hunks. These need routine independent integration review against current main. The distinct atomic capacity race in [#1274](https://github.com/Zennay/zCloud/issues/1274) is **not** fixed here.

No QA tests or regression suites were added or manually executed under the 17 October product-delivery instruction. Existing mandatory automated CI and branch/review/release gates remain authoritative. A merged source fix is not evidence of the VPS listener, worker continuation, or deployment.
