# Scheduled redispatch source guard — 16 October 2026

This **GitHub-hosted, read-only** control is independently owned deploy-ops work for [#633](https://github.com/Zennay/zCloud/issues/633), informed by the existing production recovery issue [#1216](https://github.com/Zennay/zCloud/issues/1216). It does not alter the live recovery workflow, schedule, or queue.

## What the check enforces

The workflow has **no PR path filter**: **every main-targeting pull request** starts the source-only check, including PRs retargeted to main via the `edited` event. Fork-origin pull requests fail explicitly in a pre-check on a GitHub-hosted runner (rather than silently yielding a skipped/success check); the untrusted fork is never checked out. A path-filtered guard would miss a newly added risky workflow. For same-repository PRs, the gate checks exact candidate head and canonical live main ref before and after validation, rejecting stale base evidence.

The scanner inventories regular `.github/workflows/*.yml`/`.yaml` and `.github/actions/**/action.yml`/`action.yaml` blobs at immutable Git tree IDs for base and candidate. For workflows with a scheduled event (including inline, quoted and opaque alias-shaped triggers), it identifies direct `gh workflow run`, REST workflow dispatch, REST `repository_dispatch`, Octokit dispatch and dynamic workflow-ID variants. It also recognises the split Python REST URL used by the existing recovery workflow, compact `on: [schedule]` event lists, and GitHub CLI `-R`/`--repo` flags. Scheduled callers are traversed into their **local reusable workflow and composite-action dependency graph**, including nested actions and cyclic graphs (visited once). New remote reusable workflow references are treated as opaque source risk rather than executed. All source stays local to the GitHub-hosted job.

**Post-write main audit:** the same GitHub-hosted policy also runs on every `push` to `main`, using immutable `github.event.before` and `github.sha` commits and proving advertised current main is the after-SHA. This is **detective only**: failing CI cannot undo a direct main write. The actual protection/merge controls belong to the open [#922](https://github.com/Zennay/zCloud/issues/922) integration gate, not this PR.

**Monotone fingerprint policy:** each matched dispatch site is hashed from its source line and counted as a multiset; local/remote reusable edges are fingerprinted too. Source paths are included in dispatch fingerprints to distinguish a shared helper from its callers. A new site, duplicate site, changed command or changed dispatch target is rejected—even if the old site was removed so that raw call counts appear equal. An unchanged legacy site may remain while other non-dispatch code changes. Fingerprinting deliberately treats some formatting-only changes to the dispatch line as review-requiring, not as proof of new runtime behavior.

## Read-only acceptance

- `python3 -m unittest -v tests.test_zcloud_scheduled_dispatch_debt_guard_20261016_w31`
- `python3 scripts/zcloud_scheduled_dispatch_debt_guard_20261016_w31.py --base <40-character-main-SHA> --candidate <40-character-PR-head-SHA>`

Outputs contain only repo-relative workflow filenames, bounded counts, Git SHAs and permit/deny status; they never echo workflow content, secrets, command arguments or event payloads. Exit codes: 0 unchanged/no-new-debt; 1 new sites; 2 malformed/incomplete evidence. `mutation_performed=false` and `deploy_authorized=false` always.

Source guard validates SHA syntax, regular blob mode, tree entries, content size, UTF-8, Git object lengths, and rejects control-character/newline/terminal-escape manifest paths. Reusable workflow and nested composite-action dependency traversal is bounded by a visited set (cycles are not followed indefinitely). Git subprocess stdout is **stream-read** through a bounded 64-KiB read loop, rejected immediately above 16 MiB and limited by a 20-second end-to-end deadline. The process is killed on excessive output, timeout or source failure, without exposing its stderr or the source payload.

## Explicit non-claims

This is **not** a complete interpreter for shell variables, opaque/dynamic `uses` expressions, shell scripts imported by workflows or actions, third-party action source, runtime dispatch permissions or indirect external orchestration. It only traverses explicitly declared `.github/workflows/*.yml` reusable workflows and `.github/actions/**/action.y(a)ml` composite manifests, not arbitrary files or external code. In particular, a command assembled through opaque wrappers could escape string-based matching; all privileged production workflows still require separate authorisation.

Passing this PR gate does **not** fix existing [#1216](https://github.com/Zennay/zCloud/issues/1216) redispatch, demonstrate the self-hosted runner is alive, or authorize merging/deploying anything. Actual recovery idempotence has its own [#913](https://github.com/Zennay/zCloud/pull/913) workflow owner and [#1239](https://github.com/Zennay/zCloud/pull/1239) policy owner. Release stays serialized under #580/PWQ-41 + #1089. No manual workflow dispatch, VPS/Firefox/SQLite/worker/runner/queue/service write is part of this source-only guard.
