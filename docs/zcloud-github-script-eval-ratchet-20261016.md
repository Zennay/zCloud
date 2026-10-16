# Deploy-ops: deny new direct expressions in GitHub-script JavaScript

**Scope:** Zennay/zCloud issue #633; isolated, GitHub-hosted *source-only* preventive control. No VPS, runner, queue, worker, Firefox, SQLite, production or release authority.

## Threat boundary

`actions/github-script` evaluates the value of `with.script` as JavaScript. GitHub expression expansion takes place before that execution. Event text, PR titles, issue titles and user-controlled workflow inputs inserted directly with `${{ ... }}` can break JavaScript string quoting or become executable source. Never directly inline an expression into `with.script`. Prefer a step `env:` binding and access through `process.env`, with validation, least-privilege permissions and safe handling of secrets.

## What the guard enforces

* Compares **two immutable, ancestor-related Git commit SHAs** through regular Git blobs; checks workflow YAML plus root/nested composite Action manifests (`action.yml` / `action.yaml`), including untouched baselines, with object identity, UTF-8, symlink, file-count and byte budgets.
* Finds normal, step-level `actions/github-script@` uses and their `with.script` scalar. Flags direct `${{ ... }}` expressions, Unicode-escaped expression delimiters and YAML alias/flow-map ambiguity. The existing dangerous step is recorded by its path, starting line and full-step hash; removing hazards decreases debt; new or changed dangerous steps are rejected.
* YAML action discovery ignores comments and block-scalar JavaScript data; a harmless reference to `actions/github-script@` in a JS string no longer counts as a new action. The detector still refuses quoted/escaped `uses` keys, flow mappings, aliases and live direct-expression insertions.
* Git subprocess stdout is consumed **incrementally with an enforced limit before buffering**; exceeding the byte/time budget terminates the subprocess and denies admission, rather than buffering unbounded Git output first. Failing subprocesses, malformed limits and timeouts cannot return a successful receipt.
* Reports **only bounded counts and fixed reason labels**, never JavaScript or expressions, secrets, job names, tokens, or worker information. `release_authorized=false` and `production_mutation_authorized=false` in all success receipts.
* Exit codes: `0` valid no-new-debt, `1` new risk, `2` invalid/incomplete evidence. Tests exercise real Git commits, SHA ancestry, symlinks, legacy behavior, comment/JS-string false positives and simulated oversized or failing Git subprocesses.

## Limitations

This deliberately strict lexical tripwire is **not** a full YAML parser, JavaScript parser or comprehensive security scanner. Legacy source may contain risk; green means only *no new direct source risk under this defined grammar*. Complex YAML tags, aliases, computed `uses:` or atypical quoted mappings need independent review. Safe environment-variable binding itself does not guarantee safe downstream parsing. The PR cannot prove VPS availability or authorize production. Composite-action source is included, but only direct, lexically legible `actions/github-script` steps and bounded YAML ambiguity are covered. The separately owned shell-expression guard PR #1464 covers `run:` shells, not this `with.script` boundary.

## Review and integration

Owned paths are exclusively:
`scripts/zcloud_github_script_eval_ratchet_20261016.py`,
`tests/test_zcloud_github_script_eval_ratchet_20261016.py`,
`.github/workflows/zcloud-github-script-eval-ratchet-20261016.yml`,
and this document.

Review against current `main`, check focused GitHub-hosted tests on **the exact PR head**, validate workflow provenance, and require independent review before any merge. Keep owner gates #922, #1326, #1447 and #580/PWQ-41/#1089 separate. No dispatch to self-hosted runners.
