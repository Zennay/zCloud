# zCloud deploy-ops: reusable workflow secret-inheritance ratchet (15 October 2026)

Owner: deploy-ops, isolated source-only branch, tracking [#633](https://github.com/Zennay/zCloud/issues/633).

## Concrete risk
GitHub Actions reusable workflow calls can use `secrets: inherit`, forwarding every secret available to the calling job into the called workflow. A newly added or changed call with this declaration can widen credential exposure even when the repository's `GITHUB_TOKEN` permissions remain read-only.

## Implemented, bounded scope
- Compare immutable base and head Git trees, not mutable checkout filenames. Check each new or changed `.github/workflows/*.yml` or `.yaml` Git blob for potential `secrets: inherit`, including quoted and opaque spellings. Reject unsafe changes by default.
- Grandfather historical workflow blobs **only while unchanged by SHA**; this prevents accidental widening while allowing legacy review separately.
- Reject symlink/non-blob workflow paths, invalid UTF-8, unexpected source-object types, oversized files, oversized inventories, invalid commit identifiers, and missing Git evidence.
- Fail on newly introduced or modified inheritance patterns and print a minimal fixed-wording deny reason, not YAML/secret values.
- GitHub-hosted, read-only PR gate checks same-repository base and head and exact immutable current main **before and after** tests. There is no production token, no workflow dispatch into VPS and no automatic merge.
- Real Git fixtures test new unsafe workflow, modified legacy risk, grandfathered unchanged blob, quoted/flow/indented inherit, malformed UTF-8, symlink, oversized source, deleted unsafe workflow, comments, and bounded redacted CLI.

## Limits and integration safety
This is a deliberately *conservative lexical source ratchet*, **not** a complete YAML parser or proof that legacy workflows are safe. It may reject safe YAML mentioning both terms. It does not detect every possible YAML encoding or secret leak; an independent reviewer and enforced `main` branch protection remain necessary because PR code can edit its own CI checks. The workflow runs only in hosted CI, so passing tests never validates the self-hosted listener.

This patch is separate from current owners: PR #1463 (Action ref pins), PR #1464 (shell interpolation), PR #1467 (token permissions), PR #1468 (LKG snapshots), issue #1447 (production writer locks), PR #595 (rollback), issue #1326 (runner). Four **ADD-only**, file-disjoint paths, with no existing production/release workflow modified. Merge/deploy/rollback authority remains false. Require fresh changed-file ownership, exact-head full regression and independent security review before integration. Never interpret this source-only gate as production approval.

## How to verify
`python3 -m unittest -v tests.test_zcloud_reusable_secrets_guard_20261015_w3`

`python3 scripts/zcloud_reusable_secrets_guard_20261015_w3.py --base <40-hex-main-sha> --head <40-hex-pr-head-sha>`

Both commands are offline/read-only. The GitHub-hosted PR workflow also rechecks current main on both sides of these commands.

## Extended adversarial source coverage
The ratchet also conservatively denies newly changed workflow YAML with literal Unicode/hex escape sequences, even if they occur in otherwise benign strings. This avoids a false safe verdict from obfuscated `secrets` keys or `inherit` values; it may cause false positives requiring independent review. Complex YAML mapping keys split across `? secrets` and `: inherit` are also detected. Three additional real-Git negative fixtures enforce the behavior. Both changes are source-only and confer no deployment authority.

## PR event integrity and workflow-directory preservation
- The check runs on every pull request to main, not only a limited set of path triggers. A skipped job is not proof of approval.
- Immutable GitHub main and pull-request-head remote refs (SHA **and** complete ref name) are verified both before and after tests. Changing the source branch while checks execute invalidates the evidence. A PR author must still not be considered an independent trusted reviewer.
- The source scanner now requires both `.github` and `.github/workflows` to remain real Git tree objects at base and head. A symlink or deletion of that directory must not silently produce an empty, passing inventory.
- Real-Git fixtures cover directory removal, workflows-root symlink, parent-root symlink, remote branch movement, and a valid directory. All operations remain source-only.

## Empty YAML inventory denial
Merely preserving `.github/workflows/` with only a README does not preserve GitHub Actions execution. The scanner therefore rejects a base or candidate Git tree with **zero** recognized `.yml`/`.yaml` workflows, while still permitting removal of an individual retired workflow when another actual workflow remains. This is source-only fail-closed evidence, not an authorization to keep or delete workflows. Two real-Git regression fixtures protect the distinction.

## Bounded Git subprocess output, including malicious blobs
The Git-tree scanner no longer uses `subprocess.run(stdout=PIPE)` to capture an entire Git subprocess response before applying a size limit. It now consumes stdout through a bounded selector loop, reads at most the allowed number of bytes plus one proof byte, kills an oversized or timed-out subprocess, and always waits for cleanup. A 20-second wall-clock deadline applies even when output stops arriving. Negative real-Git cases prove large blob truncation, exact-41-byte SHA output bounds, zero-byte successful `merge-base` output, invalid budgets and nonzero Git status. This limits memory exposure to a fixed cap but does not make source or repository content trustworthy, nor authorize deploy/merge.

## Git subprocess non-interactive evidence boundary
All Git source reads now explicitly disable inherited stdin, close unrelated file descriptors, disable terminal credential prompts and optional Git locks, and discard stderr. This keeps source inspection non-interactive and minimizes accidental file locking or prompt-based hangs. Real subprocess inspection tests assert those environment and descriptor settings. The scanner still relies on untrusted PR code until a separately reviewed version is installed on protected main; this is not an authorization or deploy guarantee.

## Prevent Git replacement-object evidence spoofing
A checkout can contain local `refs/replace/` objects that cause ordinary `git cat-file` to return a different blob while preserving the original requested object ID in the tree. All immutable source reads now set `GIT_NO_REPLACE_OBJECTS=1`. A real-Git adversarial test writes an unsafe reusable-workflow YAML blob, replaces it with innocent bytes through `git replace`, proves plain Git sees the substitute, and verifies the scanner still denies the original unsafe SHA. This only strengthens local read-only evidence; it does not authenticate PR code or grant merge/deploy authority.

## Independently verify Git blob bytes
Disabling local `refs/replace` is not sufficient when a malformed or substituted object is returned by the local Git-object database. The source scanner now recomputes the canonical SHA-1 Git blob identity from `blob <byte length>\\0<raw bytes>`, and compares it with the 40-hex object ID recorded in the candidate tree. It also cross-checks the separately reported blob size against the received byte length. A mismatch denies with a fixed `git_blob_integrity` reason and never prints source contents. Regression tests simulate size spoofing and same-sized content substitution. A separate real-Git tree replacement test proves normal Git follows the replacement while the guarded source reader inspects the true Git object. These proofs are repository-source only: independent review, protected-main policy and serialized deployment gates remain necessary.

## Unambiguous Git workflow path inventory
Git permits workflow filenames containing embedded newline, tab and bidirectional formatting characters. These filenames can make source review and incident diagnostics misleading. Tree enumeration now denies such control characters (including U+202A–U+202E and U+2066–U+2069) before deciding whether the path has an ordinary YAML extension. Unicode letters remain permitted. Four real-Git filename fixtures cover newline, bidi override, tab in a non-YAML path and an accented valid YAML filename. This is a conservative inventory/trust boundary, not a replacement for GitHub's own path validation.

## Ignore caller-controlled Git environment
The read-only Git subprocess now drops inherited `GIT_*` variables before launching and explicitly restores only strict read-only source-inspection values. This removes alternate object-database pointers, trace destinations, dynamic `GIT_CONFIG_COUNT` injection and caller changes to replacement-object settings. Two synthetic-environment/real-Git tests verify the original commit can still be read, the child receives the trusted settings, and no trace file is written. This does not sanitize non-Git environment variables or convert PR-owned source into independently reviewed trust. It provides no production authority.

## Exact origin URL before and after the source check
The PR admission shell now binds `git remote get-url origin` to the canonical `https://github.com/Zennay/zCloud` both before and after the Git object scanner runs. This prevents tests or source code from quietly switching `origin` to a different repository while keeping the same expected ref names. It complements the existing exact main and PR-head SHA/ref checks, but the PR-supplied workflow is not itself a protected, independent check until reviewed and landed through the controlled branch-protection process.

## Visual ambiguity and cross-platform workflow-path collisions
A reviewer can mistake two Git-distinct workflow names for one another if their spelling differs only by Unicode normalization or casing. The tree inventory now rejects **new or modified** groups whose normalized NFC + casefold path keys collide, including `Policy.yml` versus `policy.yml` and precomposed versus decomposed accented Unicode. Unmodified, pre-existing colliding names remain grandfathered *only as identical mode/blob mappings*; changing either member ends that exception. Git filenames containing Unicode format/invisible controls (including zero-width space and soft hyphen) are also rejected up front using Unicode character categories, rather than relying on a short manually maintained bidi-character list. Seven real-Git regression cases show both the denied and permitted paths. The checker is intentionally conservative and never authorizes merge or production writes.

## Strict offline Git configuration boundary
Scanner subprocesses now explicitly disable machine-wide and global Git configurations (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_SYSTEM=/dev/null`, `GIT_CONFIG_GLOBAL=/dev/null`) while continuing to ignore inherited `GIT_*` overrides. They also set `GIT_NO_LAZY_FETCH=1` to prevent Git versions supporting this option from performing implicit network/promisor fetches when an expected object is absent. The source check must fail rather than retrieve content from an untrusted remote. Two tests prove externally supplied global gitconfig is visible to ordinary Git but invisible to the guarded reader and that the child subprocess receives all expected isolation options. Runtime verification of offline behavior on every Git version remains a separate limitation; this never grants deploy, merge or governance authority.

## Honest ratchet coverage receipts and safe triage
A green ratchet verdict **does not scan all existing workflow bodies**. Every passing receipt now explicitly reports `source_scope: changed_workflows_only`, `workflows_examined` (metadata inventory), `changed_workflows_checked` (actual Git blob content reviewed), and `legacy_workflows_not_rescanned` (unchanged blobs never content-reviewed in this run). These values make the grandfathering policy visible to the operator instead of implying blanket workflow safety. When one or more changed workflow files may contain inheritance, the deny receipt prints 24-hex-character truncated SHA-256 **path fingerprints**, sorted by path; it never prints YAML contents or full paths. Operators can correlate a fingerprint to a PR-changed filename locally. Four regression tests verify correct counts for old unsafe debt, stable multi-file fingerprints, content redaction and generic denials without source disclosure. Passing still never authorizes merge or deployment.

## Whole source scan timeout, not just individual subprocess timeouts
A 20-second timeout on each individual Git process can still allow hundreds of processes to exceed a GitHub-hosted job's overall timeout. The source scanner now also sets one fixed 180-second monotonic deadline per `inspect(base, head)` run and passes it into **every** Git subprocess: ancestry proof, base/head workflow-root verification, source trees and each changed blob's reported size and actual bytes. Each Git subprocess obeys the earlier of its 20-second local deadline and the shared scan deadline, denying as soon as admission is already expired. Three deterministic fixtures verify pre-launch expiry, propagation of exactly the same global deadline through all actual Git reads and continuation of normal green inspection. It remains a read-only, nonauthorizing source check, and no VPS production health is inferred.
