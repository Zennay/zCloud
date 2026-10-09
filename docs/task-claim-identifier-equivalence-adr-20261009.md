# Task-claim identifier equivalence — decision proposal (offline only)

Status: **proposed, not admitted to production** · 2026-10-09 · Tracks #1228.

## Existing observable contract

The documented endpoint `POST /api/task-claims` accepts `project_id`, `claim_key`, `owner_id`, optional `worker_id`, and `lease_seconds`. SQLite uniqueness is based on the **pair** (`project_id`, `claim_key`), and the GET list is project-scoped. Expired ownership is reclaimable; owner-only heartbeat/release and the 15–3600 second lease bound are contractual. This record does **not** claim server-side Unicode behavior is already verified. The runtime implementation, JSON decoder, SQLite collation and every consumer still require a source and fixture inspection before enforcement.

## Recommended identity policy for future review

| Property | Proposed behavior | Reason / compatibility impact |
| --- | --- | --- |
| Encoding | Valid Unicode scalar strings decoded from strict UTF-8 JSON; reject malformed UTF-8 and unpaired surrogate escapes | Prevent decoder-dependent keys |
| Normalization | Require input to already be NFC; **reject** non-NFC rather than silently transform | Avoid changing the ownership key on lookup or release; migration audit required |
| NFKC | Do not use for identifier equivalence | Compatibility folds change intentional distinctions |
| Case | Exact case-sensitive comparison; no case-folding | Preserve existing mixed-case references |
| Whitespace | Reject leading/trailing or invisible whitespace; allow documented internal ASCII delimiters | Current unknown consumers require audit before changing |
| Controls | Reject U+0000–001F, U+007F–009F, bidi format controls, zero-width joiner/non-joiner and other Default_Ignorable_Code_Point code points | Protect reviewer-facing ownership evidence |
| Delimiters | Preserve `:`, `/`, `-`, `_`, and `.` as distinct literal characters, never split or concatenate untrusted segments | Existing composite keys such as `notion:api` must remain representable |
| Length | Propose a bound in UTF-8 bytes per component, not code points; select actual limit only after measuring historical identifiers | Avoid truncation collisions or surprise legacy rejection |
| Cross-project | Compare the exact tuple, never a display-concatenated string | Avoid false release or cross-project ownership |
| Display | Escape control/format characters in diagnostics, preserve exact canonical ID in machine data, and never rely on visual equivalence | Homoglyphs may remain distinct without falsely implying same identity |

**No enforcement is authorized by this ADR.** A non-NFC or format-character legacy key may already be active. Before applying a policy, inventory existing SQLite keys and every client (including Notion, browser extension, and GitHub preflight), choose fail-closed migration/expiry semantics and implement versioned rollout. Do not rewrite active rows, normalize-on-release or coalesce lookalike rows.

## Differential fixture matrix (future isolated test)

Use an in-memory temporary SQLite database and mocked HTTP request handler only; **never** connect to the VPS, use a management token, or import production `history.db`. For every fixture assert acquire/GET/heartbeat/release use the same tuple and never affect another project's row.

| Case | Representative fixture | Expected proposed admission |
| --- | --- | --- |
| Composite ASCII | `notion:api`, `github/actions` | Accept |
| Case distinct | `Job-A` vs `job-a` | Distinct |
| NFC vs NFD | `é` vs `e\u0301` | NFC accepted; NFD denied rather than aliased |
| Confusable scripts | Latin `a` vs Cyrillic `а` | Distinct in storage; review display must show code point warning |
| Bidi | U+202E in key | Reject |
| Zero-width | U+200B / U+200D in key | Reject |
| JSON surrogate | `"\\ud800"` | Reject |
| Malformed raw UTF-8 | Decode request bytes with invalid octet | Reject |
| Boundary | Very long UTF-8 multi-byte key | Reject at documented byte cap (to be chosen) |
| Cross-project | Same key under `cloud` and `ftmo` | Independent |
| Delimiter spoof | `a:b` under `c` vs `a` under `b:c` | Independent |
| Lease isolation | Wrong project, wrong owner, expired/reclaimed owner | No heartbeat/release of other owner's lease |

## Gate before implementation

1. Recheck **all paginated** open PR changed-file ownership and PR-less branches; this offline ADR owns no runtime source, lease tests or active preflight.
2. Validate actual JSON parsing, character policy, SQLite collation, list serialization, and extension/preflight calls against fixture-only tests. Report observed results separately from this proposed policy.
3. Coordinate with #1207 (concurrency/lease), #917 (preflight), and #580/#576 (serialized runtime gate).
4. Require exact-final-head CI and permanent self-hosted regression evidence before proposing any live change. Merge/deploy only during an explicitly cleared integration window.
