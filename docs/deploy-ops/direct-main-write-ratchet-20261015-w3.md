# zCloud deploy-ops: conservative direct-main-writer no-new-debt ratchet

**Owner:** parallel deploy-ops 1/2, independent branch \`deployops/direct-main-write-ratchet-20261015-w3\`.
**Tracking:** #922 (main protection compatibility), #633 (workflow trust debt). This branch **does not claim** the existing #1072/#1033 inventory owners or any writer, runner, deploy, rollback, queue, SQLite, browser or service workflow.

## Concrete capability

The immutable-blob checker \`scripts/zcloud_direct_main_write_ratchet_20261015_w3.py --base <40hex> --head <40hex>\` lists ordinary committed workflow files below \`.github/workflows\` and local composite Action metadata at \`.github/actions/*/action.yml\` or \`action.yaml\` at the two Git commit identities and inspects **only added/modified workflow blobs**. Completely unchanged legacy workflow bytes are grandfathered without being re-certified. New or changed workflow bodies are denied when a line contains:

- direct \`git push\` (including \`git -C ... push\`, forced, any target branch or dynamic ref), conservatively including pushes to non-main branches;
- Git refs endpoint mutation strings involving common API clients, especially \`git/refs/heads/\`;
- dynamic \`gh api\` calls with visible mutating HTTP method selectors even when the ref endpoint is variable;
- Octokit \`github.rest.git.updateRef/createRef/deleteRef\` calls (including \`octokit\` aliases) targeting Git refs.

Changed blob symlinks, non-UTF-8/NUL, oversized source, missing workflow inventory and invalid commit IDs deny. Git replace refs and inherited \`GIT_*\` configuration are ignored when reading committed evidence. Each JSON receipt declares \`authorizes_merge=false\`, \`authorizes_deploy=false\` and \`mutation_performed=false\`. The tool performs no network, API, SQLite, environment or runtime writes.

The companion hosted-only PR job first checks canonical main and checked-out PR head against GitHub event SHA, runs actual temporary-Git adversarial tests and the immutable source checker, then rechecks both SHAs. It requests \`contents: read\`, does not persist checkout credentials, and never targets a self-hosted runner.

## Known limits and remaining prerequisites

This is a **conservative lexical check, not a complete YAML/shell/PowerShell interpreter**. Dynamic multi-line concatenation other than POSIX backslash-newline continuations, shell aliases, alternate Git implementation wrappers, encoded commands and hidden API endpoints can evade lexical matching; shell examples inside executable \`run\` bodies can cause false positives. It excludes standalone scripts outside workflows/composite Action metadata, indirect imported files and deleted workflow files, and cannot classify whether an existing writer is safe to retire. The receipt deliberately states the number of unchanged legacy blobs not re-scanned.

A PR author can alter their own PR source and tests. Consequently **a green PR-controlled gate is not independent trusted branch protection**. Do not mark #922 complete or merge on this evidence alone. Enforce independent review/protected required checks only after #1033 compatibility and existing workflow owners clear direct-main writers. Actual release additionally requires #580/PWQ-41, #1089, production writer locks #1447 and runner/source acceptance #1326.

The change is strictly additive and review-only; **no privileged workflow dispatch, live deployment, GitHub branch-rule modification, VPS/Firefox/service/SQLite/queue/runner/worker mutation** is authorized.

## Hardening follow-up

The first hosted proof was green on initial four-file HEAD. A subsequent source-only hardening wave introduced bounded stdout collection with child process cleanup, size checks before blob loading, more Git option/quoted-argument cases, POSIX backslash-newline folding and GitHub Octokit ref-mutation detection. Exact-head focused/full CI is required for each final source SHA; historic green tests cannot establish acceptance for newer commits.

## Composite Actions coverage

A further source-only extension also scans newly added/edited local composite Action metadata (nested directories supported). Safe composite Actions can pass, direct Git/Octokit writers fail, and symlinked Action paths or Action directories fail closed. Existing unchanged composite files remain legacy debt, not certified safe. A valid workflow inventory is always mandatory even if Actions exist. Six additional real-Git fixtures cover these conditions.

## 15 October follow-up — immutable ancestry and dynamic GraphQL

New direct-write paths can hide behind the GitHub GraphQL endpoint: `gh api graphql`, `github.graphql(...)`, `octokit.graphql(...)`, or direct `api.github.com/graphql` HTTP calls. This conservative source-only policy denies **all new or edited appearances** of these entrypoints because a static lexical checker cannot verify dynamic query bodies, including indirect query files. Legitimate read-only GraphQL is therefore a documented false positive requiring independent policy-owner review; unchanged old blobs remain legacy debt. Ordinary REST `gh api` GETs remain allowed.

The scanner also demands that the `--base` commit is an actual ancestor of `--head`. A divergent but valid 40-character commit SHA can no longer launder unrelated historical workflow blobs as "unchanged" debt. The nine new temporary-Git fixtures cover divergent histories, valid ancestors, GraphQL CLI/Octokit/HTTP entrypoints, and a read-only REST negative control.

## Further parsing hardening — Git global configuration arguments

Git permits global arguments with whitespace-containing quoted values (for example `git -c 'user.name=Alice Example' push origin HEAD:main`). The old line regex could miss that valid direct writer. The new fallback tokenizes the same command with `shlex`, separates POSIX shell punctuation and detects `push` even after quoted option values or a separate `--work-tree` path. Ambiguous unmatched Git quoting fails closed. Benign `git status && printf push`, quoted configuration values containing the word `push`, and read-only Git commands remain permitted. Six additional real-Git tests cover these distinctions. This is **not** a full shell parser or complete protection against dynamic command construction.

## Hardened CI examples and whole-scan time budget

The exact-head hosted tests exposed two **false-positive scanner bugs**, not actual GitHub write attempts: a quoted `git -c 'alias.demo=echo push' status` value was parsed as a real push; after fixing that, scanning its own workflow incorrectly denied harmless `actual_head="$(git rev-parse HEAD)"` because the parser started inside a surrounding double-quoted shell expression. Both were repaired in the owned scanner and backed with specific safe-reader and forbidden-writer cases. Self-scanning is required in every hosted gate.

Git child processes were already bounded to 15 seconds each. A single immutable 120-second monotonic **end-to-end scan budget** now applies across every Git command (including before process spawn); it cannot be raised by workflow input. Expired budget fails closed with no Git process, covered by a dedicated unittest with a mocked process boundary. Valid Python, Git or shell source checks never authorize a deployment.

## Exact remote PR-head binding

The hosted-only gate now compares **the current remote PR branch** directly to the GitHub event's immutable head SHA both before and after scanning. Previously the workflow verified only its local checked-out HEAD, which stays fixed even when another actor moves the remote branch; this could yield a stale green result for an outdated PR commit. A GitHub API/branch-protection policy is still required for trusted admission: the PR-controlled source gate itself cannot act as independent merge approval.

Read-only `git ls-remote --exit-code` targets the canonical public zCloud GitHub remote URL explicitly; it demands one exact tab-separated `<SHA> refs/heads/<head-ref>` row. A missing, moved or changed branch denies. New temporary bare-Git fixtures simulate a real branch advance between two proofs and confirm that an old SHA is rejected while the current SHA succeeds. No tag, branch, runner, GitHub permission, repository setting or VPS mutation occurs in production.

## Additional indirect-write and governance guard

Git permits inline aliases to invoke `push` without a direct `git push` command, for example `git -c 'alias.ship=push' ship ...`, or to execute a shell command with `alias.ship=!bash ...`. New/edited workflow and composite source containing push-expanding Git aliases or opaque shell Git aliases now fails closed. Read-only aliases such as `alias.demo=echo safe` remain valid. This ratchet does not resolve Git aliases loaded from external configuration or earlier jobs; it is strictly a bounded source check.

The same changed-source check also denies explicit GitHub Octokit `updateBranchProtection`, `deleteBranchProtection`, `createRepoRuleset`, `updateRepoRuleset`, and `deleteRepoRuleset` mutations. This reduces opportunities for a newly added workflow to silently alter the repository rules while #922's actual protection remains separately owned. This scanner is not a replacement for GitHub rulesets and does not authorize their deployment or alteration.

## Unicode source integrity

New/edited workflow and composite Action UTF-8 bytes containing Unicode format controls (`Cf`), bidirectional overrides/isolation characters, or U+2028/U+2029 separators are rejected because such invisible content can make reviewers see a different command from the actual source. Ordinary accents in comments (for example `résumé café`) remain allowed. Unchanged historical Git blobs still retain their explicit legacy exemption and are not claimed clean. Five additional real Git regression cases capture these boundaries.

## Split Git configuration environment variables

Git supports a programmatic environment configuration set with `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0`, and `GIT_CONFIG_VALUE_0`. A workflow may split an alias definition (`alias.ship` = `push`) across multiple YAML lines so that a checker looking only for a single-line `git push` misses the effective command. This checker conservatively denies any introduction of these indexed configuration keys in changed workflow/composite source, even if the specified Git configuration was benign. `GIT_TERMINAL_PROMPT` and other ordinary non-indexed Git settings remain unaffected. Five real-Git fixtures cover the denial and a benign negative control.

## Protect against indirect main writes through PR merges

A new or edited workflow can write to `main` without spelling `git push`: `gh pr merge`, Octokit `pulls.merge` or `repos.merge`, and GitHub REST `repos/<owner>/<repo>/pulls/<number>/merge` and `repos/<owner>/<repo>/merges` endpoints can integrate code through merge operations. This source ratchet conservatively rejects these directly visible paths (including `gh -R`/`gh --repo` selectors) in changed workflows/composite Actions. Read-only `gh pr view` and `github.rest.pulls.get` stay accepted. It is **not** a full shell/HTTP parser, cannot see commands generated dynamically or imported from separate scripts, and cannot replace independent branch protection or review. Nine additional real-Git fixtures cover direct merge commands, REST/Octokit variants, and safe negative controls.

## Low-level Git transport writers (`send-pack`)

`git send-pack` can update remote refs without running the high-level `git push` verb. New/changed workflows and composite Actions using this command, including `git -c 'user.name=...' send-pack` and line continuations, are rejected as `git_send_pack`. An inline `alias.<name>=send-pack` also fails. Words in plain output (`printf 'send-pack'`) and read-only `git -c 'alias.demo=echo send-pack' status` are permitted. Source tokenization remains conservative; execution through generated scripts or system Git configuration is out of scope. Six adversarial real-Git fixtures establish these boundaries.

## Complete Git refs REST endpoint namespace

Create-reference API requests often POST to the GitHub REST **collection** `repos/<owner>/<repo>/git/refs`, with no trailing `heads/` component. A tag/notes ref write can target `git/refs/tags/...` or `git/refs/notes/...`. All these paths are relevant to repository write safety. The source-only matching now recognizes the whole `git/refs` namespace, including a URL-encoded slash (`%2f`) or literal escaped Unicode slash (`\\u002f`), when coupled with a visible GitHub API/curl/wget context. A non-ref `git/reflogs` request remains outside this rule. The lexical check may also flag read-only Git refs API reads: conservative over-denial is intentional until a trusted parser can distinguish their verbs. Six temporary-Git tests exercise these cases.

## Python and JavaScript child-process argument lists

Workflow source can execute `git push` or `git send-pack` without writing a contiguous command string, for example `subprocess.run(['git', 'push', ...])` in Python or `await exec.exec('git', ['push', ...])` / Node `spawn('git', ['send-pack', ...])`. New/edited workflow and composite metadata containing these visible argv forms now fail as `separate_argv_git_writer`. Corresponding `git status` and `git rev-parse` argv calls stay allowed. This narrow lexical rule does **not** resolve built argv lists, shell variables, aliased Git binary paths or external source files. Six genuine temporary-Git fixtures cover both write and safe read forms.
