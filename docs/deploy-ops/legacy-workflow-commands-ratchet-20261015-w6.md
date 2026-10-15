# GitHub Actions legacy command no-new-debt gate — 15 October 2026

**Scope:** additive GitHub-hosted, source-only ratchet for [zCloud #633](https://github.com/Zennay/zCloud/issues/633). The new branch owns only four unique new paths: this document, a scanner, a temporary-Git test suite and a read-only PR workflow. No pre-existing workflows, live runners, release scripts, workers, SQLite, Firefox or VPS resources are modified.

## Why
GitHub Actions' deprecated workflow logging commands `::set-env`, `::add-path`, `::set-output` and `::save-state` are unsafe legacy patterns. Re-enabling `ACTIONS_ALLOW_UNSECURE_COMMANDS` is particularly risky. Even if old workflow blobs cannot be remediated during another owner's live-workflow window, adding or editing workflows should not create *new* debt.

## Behavior and tests
- Against an immutable ancestor `BASE..HEAD`, inspect only **added/modified regular Git blobs** in `.github/workflows/*.yml|yaml` and `.github/actions/**/action.yml|yaml`.
- Reject a changed workflow/action containing any legacy command name, any case-variant of `ACTIONS_ALLOW_UNSECURE_COMMANDS`, invalid UTF-8, Unicode format controls, oversized blobs, symlinked roots or changed workflow files, malformed refs or disconnected source trees.
- Unchanged historical blobs are *grandfathered, not certified safe*. Files newly edited after this gate is installed are subject to review. The lexical detector is intentionally conservative and may reject comments/examples, while dynamically assembled commands may evade it; this is one defense layer, not a YAML, shell or Actions security parser.
- Test with an actual temporary Git repo, including both safe and adversarial workflows, symlinks, deleted files, composite Actions and historical grandfathering.
- The dedicated PR job uses GitHub-hosted `ubuntu-latest`, `contents: read`, exact SHA-pinned checkout and `persist-credentials: false`. Remote main and PR head must match PR event SHA **before and after** tests. It cannot run a privileged workflow or approve itself.

## Deployment contract
No PR-controlled check grants trusted merge approval, branch protection, owner consent, VPS/runner acceptance or production-dispatch authority. Keep [#922](https://github.com/Zennay/zCloud/issues/922), [#1326](https://github.com/Zennay/zCloud/issues/1326), [#1447](https://github.com/Zennay/zCloud/issues/1447), and #580/PWQ-41/#1089 independently gated. Leave PR draft until hosted CI and independent trust review, do not merge/deploy automatically.
