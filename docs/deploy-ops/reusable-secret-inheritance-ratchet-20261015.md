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
