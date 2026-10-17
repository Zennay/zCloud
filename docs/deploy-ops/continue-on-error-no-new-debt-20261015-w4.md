# Deploy-ops: no new silent-failure workflow debt (15 October 2026)

Status: **source-only proposal; no authority to deploy, merge, release, run VPS jobs or conclude runner health**. Parent risk area: #633. Parallel ownership: all new paths have suffix 20261015_w4; existing .github/workflows and scripts are untouched.

## What changes

A new GitHub-hosted pull-request check inspects Git objects at the exact PR base and head, then refuses an **added or edited** workflow when its `continue-on-error` key is anything other than a literal YAML `false`. This catches `true`, expression-controlled failure suppression, duplicate shadowing, quoted keys, inline mapping keys, and ambiguous textual/Unicode-escaped forms. YAML merge keys fail closed for changed files. Legacy workflow blobs are grandfathered **only while unmodified**; the existing deliberate best-effort operator-summary step in zcloud-vps-deploy.yml remains untouched.

Unambiguous `run: |`, `run: |-`, and `run: >-` shell body lines are treated as inert YAML scalar text, using **the first nonblank content line's indentation** as the body boundary; even a sibling policy key indented farther than `run: |` is re-evaluated on dedent. Explicit indentation indicator forms (`|2`, `>2`) are intentionally not skipped and remain fail-closed. This is intentionally stricter than a normal YAML semantic parser: unproven encodings and merge aliases may require human remediation; quoted `name:` display examples and unambiguous `run: |`/`run: >-` body examples are inert. It is a deny-only change detector, not proof that all YAML or GitHub expressions are safe. No workflow control or release privileges are granted. Denials expose only a fixed, reviewed reason code (e.g. `git_output_limit`, `git_elapsed_deadline`, `invalid_workflow_utf8`), never exception payloads, source contents or subprocess stderr.

## Admission boundaries

- Exact 40-hex base/head, SHA-bound current checkout, proven Git ancestry, changed-only paths from immutable Git history.
- At most 250 workflow paths; immutable workflow-root tree (not a symlink), blob size preflight before read, replace-ref-resistant Git reads, bounded streamed Git stdout and a monotonic 15-second subprocess deadline (including the pre-first-byte hang), explicit child cleanup; UTF-8 source without invisible format/BiDi or ambiguous line-separator Unicode and normal executable/non-executable blob modes only; no workflow symlink.
- New CI runs only on GitHub-hosted ubuntu, rejects foreign PR origin by a failing pre-check *instead of a skipped green job*, requires PR/same-repository/main base before any checkout, pinned checkout without persisted credentials, read-only token; verifies exact SHA before and after tests, and rechecks both canonical remote main and exact remote PR head before checkout **and again** after tests (a moved ref denies stale acceptance).
- Tests use real temporary Git repositories and cover altered historical blobs, rename, false, true, expressions, duplicate keys, YAML merge, encoded keys, comments, malformed SHA and symlinks.
- As a PR-defined check, this is **not independent trusted review**. It must be reviewed and integrated under #922 with appropriate branch protection to prevent PR tampering. Do not conflate success with #1326 runner repair, #1447 writer serialization, or #580/PWQ-41/#1089 release approval.

## Follow-up

Independent maintainer reviews strictness and possible legitimate false positives; once main protections are authoritative and CI green at exact HEAD, integrate source-only. Keep VPS services, Firefox, worker ownership, SQLite, queue, and release untouched.
