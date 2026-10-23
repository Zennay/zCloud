# zCloud deploy-ops — deny-first release evidence receipt (offline contract)

This contract is an **offline, read-only diagnostic**. It does not query GitHub, validate the honesty of an evidence producer, acquire the production lock, grant approval or deploy anything. Its strongest possible verdict is **offline_claims_complete=true**; **production_mutation_authorized and live_sources_verified are always false**. Do not use a passing receipt as live release admission.

## Why this matters

Hosted PR CI can be green while the actual VPS listener remains unassigned and canonical main is unprotected. The same workflow may also be missing the seven-way non-cancelling writer serialization and explicit owner release window. This small consumer refuses to compress those distinct prerequisites into one misleading "green" flag.

## Machine contract

Run only against a locally generated, fresh, bounded JSON receipt:

    python3 scripts/zcloud_release_gate_receipt_20261016.py --input /path/to/release-receipt.json

For Dutch operator-facing audit text instead of JSON codes:

    python3 scripts/zcloud_release_gate_receipt_20261016.py --input /path/to/release-receipt.json --format markdown

**Deny-first CLI semantics:** normal invocation now exits **2 even if all supplied claims look complete**. This avoids a shell deployment preflight treating a forged or unverified offline receipt as an authorized green gate. Both JSON and Dutch Markdown still display the complete offline classification, with `production_mutation_authorized=false` and `live_sources_verified=false`.

To explicitly obtain exit 0 *only as an offline schema/claim-completeness diagnostic*, pass:

    python3 scripts/zcloud_release_gate_receipt_20261016.py --input /path/to/release-receipt.json --expected-main-sha <operator-supplied-40char-lowercase-sha> --exit-zero-on-offline-claims

With that flag **and** an operator-supplied `--expected-main-sha`, exit 0 means **only the caller-supplied unverified claims are structurally complete and match that separately supplied immutable main revision**. An absent, malformed, uppercase or mismatching expected SHA refuses the diagnostic with exit 2. Neither the supplied SHA nor the receipt proves current GitHub main: a trusted human must independently retrieve and confirm it. Incomplete, unsafe, stale or malformed input still exits 2. Never use this flag as a release approval condition or in a production-authorizing CI job. No combination of flags can grant release, verify live GitHub/VPS signals, obtain review, or perform a write. No network access or state mutation is performed.

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

The JSON parser enforces an explicit **64-level maximum JSON container nesting** before deserialization, counting brackets only outside escaped/quoted strings so the limit is independent of the host Python recursion settings. Excessive nesting denies as `invalid_json` without stack traces. The JSON integer parser is capped at **128 decimal digits**, including negative integers, independently of the interpreter's configured integer conversion limit. The JSON parser additionally rejects exponent overflow to nonfinite floats (e.g. `1e9999`), excessive recursive nesting and overlong integer literals without an uncaught traceback, while preserving specific codes for duplicate keys and NaN/Infinity. An untrusted malformed receipt still returns both `live_sources_verified=false` and `production_mutation_authorized=false`; neither positive nor negative offline outcomes can approve a live release.

Deterministic tests cover absolute/relative nested valid evidence, parent and leaf symlinks, missing path and traversal, FIFO nonblocking denial, a simulated parent-directory swap *after its directory descriptor was opened*, JSON deep recursion/large integers/nonfinite exponent and CLI deny-path redaction. This is hardening of the existing dedicated PR, not a new overlapping privileged runner workflow.

## Unicode and audit-display spoofing boundary (16 October follow-up)

Decoded JSON strings and object keys are now scanned iteratively after parsing. Even a syntactically valid JSON text can contain escaped **isolated UTF-16 surrogate values** (for example `\\ud800`), invisible controls, Unicode format modifiers such as bidirectional overrides and isolate marks, or embedded NUL. They are rejected as stable `invalid_json` rather than preserved in offline receipts or exposed to future report consumers. The bound covers nested objects, nested arrays, keys and values without recursive traversal, avoiding an interpreter-dependent stack failure. Valid international text (for example `café`) and well-formed surrogate pairs representing an actual Unicode character (for example a rocket emoji) remain valid.

The validator still does not trust input claims and emits no production authority. Adversarial tests cover nested escaped surrogates, bidi overrides, embedded controls, allowed Unicode text, and both JSON/Markdown CLI error redaction. This continues to use the four exclusively owned paths of [PR #1484](https://github.com/Zennay/zCloud/pull/1484); it does not modify other workers' trust parsers, Actions runner listeners, writer locks or production services.

## Audit verdict consistency (16 October follow-up)

The Dutch Markdown report renderer now rejects internally inconsistent or forged verdict objects **before** marking any gate as claimed-complete. It requires exactly the six documented result fields, canonical contract and untrusted evidence provenance, both release/live-verified flags strictly false, unique known reason codes, and a matching `offline_claims_complete` value derived from the absence of reason codes. Missing/extra fields, duplicate codes, unexpected provenances, false-green statuses or impossible combinations turn all eight rows into **Niet beoordeelbaar**, with the permanent **GEEN PRODUCTIEVRIJGAVE** decision. All report text remains from static source strings, never receipt values.

This prevents an embedded or future consumer of the renderer from accidentally displaying *complete claim* rows for malformed, spoofed or contradictory internal result objects. Even a fully consistent verdict remains **only an untrusted offline claim**, not independent GitHub/VPS/live proof or release authority. Tests exercise both malicious and legitimate positive/negative result shapes.

## Nederlandse blockerdiagnose (16 oktober — releaseadmission veilig gehouden)

Wanneer `--format markdown` een onleesbaar, ontbrekend, onveilig of ongeldig lokaal bewijsbestand ontvangt, krijgt de operator voortaan niet alleen achtmaal **Niet beoordeelbaar**, maar ook één van zeven **vaste, Nederlandse invoerproblemen** met een concrete lokale herstelactie: JSON-structuur, onveilige tekens, ongeldige getallen, dubbele sleutels, te groot bestand, onveilig/ontbrekend pad, of niet-leesbare bron. De uitvoer bevat nooit ruwe JSON, bestandspaden, tokens of onbetrouwbare strings. Ook een inconsistent intern rapport wordt als onjuiste structuur uitgelegd. Alleen de invoer is mogelijk direct lokaal te herstellen; dit zegt niets over branchprotection, echte VPS-luisterstatus, zeven deploy-locks of het productievrijgavevenster.

Dit lost een triageknelpunt op zonder brondata te delen of de denial-policy te verzwakken: **GEEN PRODUCTIEVRIJGAVE** en beide live-/productiemutatievlaggen blijven negatief. Dezelfde gerichte CLI en unittest-regressies bewijzen de foutcategorieën en redactie bij echte ongeldige JSON en een symlink-pad.

## Release-admission exit-code confusion fix (16 October follow-up)

The original CLI returned exit 0 on a forged but schema-complete all-true receipt. Although JSON clearly marked `production_mutation_authorized=false`, shell scripts sometimes gate writes using only subprocess exit status. The CLI is now **fail-closed by default for every possible unverified receipt** (`exit 2`). An intentionally named `--exit-zero-on-offline-claims` opt-in retains useful exit 0 semantics for **offline diagnostics only**; invalid/missing gates still return 2. Dedicated CLI tests verify JSON and Dutch Markdown default denial, opt-in negative cases, and that a real `bash -e` command cannot advance past a claimed-green default receipt. This is a backward-incompatible change within an unmerged standalone PR; no existing zCloud production workflow calls this new script. Integration owners must never treat opt-in claim success as independent release evidence.

## Offline SHA binding for explicit diagnostic success (16 October follow-up)

The explicit `--exit-zero-on-offline-claims` opt-in now **also requires** `--expected-main-sha <40-lowercase-hex>` and compares that separately supplied value to **both** the untrusted receipt's candidate and canonical main SHAs. If the operator omits the expected SHA or gives a malformed, uppercase, stale or mismatched SHA, `candidate_not_exact_current_main` is returned, `offline_claims_complete=false` and process exit is **2**. This closes a practical false-positive scenario where a claimed-complete stale receipt could otherwise return offline diagnostic success with no outside reference at all. The default remains exit 2 for every claim, and the tool never contacts GitHub or proves that the operator's value reflects actual current main. Exact revision confirmation and independent approvals stay with the separately authorized release operator. Tests cover valid, missing, malformed, wrong, uppercase and drifted revisions as well as both JSON and Dutch CLI modes.
