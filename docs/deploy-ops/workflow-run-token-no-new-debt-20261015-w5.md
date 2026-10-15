# Deploy-ops: no new privileged workflow_run token defaults (15 October 2026)

**DRAFT / NOT PRODUCTION / NO MERGE AUTHORITY.** Scope: #633. Separate from #1456 privileged pull_request_target admission, #1467 generic write-token ratchet, and #1269 queue-watchdog remediation. Four uniquely named add-only paths, no shared writer / VPS / runner code.

## Problem and policy

A GitHub Actions workflow triggered via `workflow_run` executes in its own privileged context, independently from its upstream CI. Its token may be write-capable even if the upstream triggering workflow was read-only.

This conservative source-only PR ratchet requires newly added or edited `workflow_run` workflows to declare explicit read-only permissions at the workflow root (`permissions: contents: read`, other read-only scopes or `read-all`) and to have **no workflow-level or job-level write grants**. It denies missing permissions and ambiguous aliases/merges, while preserving byte-identical historical workflow blobs; it does not modify or endorse historical exposure.

## Evidence and limitations

- Immutable SHA-checked Git source at exact base and head; ancestor check, workflow tree/mode preflight, bounded inventory and blob size, **stdout capped while reading (not after capture) and a 15-second Git deadline with forced child cleanup**, UTF-8-only. Symlink workflow files denied.
- Tests use genuine temporary Git repositories and include read-only-safe cases, missing defaults, write-all, job writes, aliases, quoted keys, inline/multiline trigger forms, legacy-grandfathering and symlinks.
- GitHub-hosted pull-request check has only `contents: read`, pinned checkout and `persist-credentials: false`; foreign PRs fail **before** source checkout instead of skipping as successful.
- This is not a complete YAML semantics parser, full GitHub token provenance proof, production admission, protected-main attestation, or evaluation of workflow_run payload/trigger trust. Ambiguous YAML may be denied conservatively.
- Independent review and proper protected-main #922 remain prerequisites before integration. #1326 runner health, #1447 production writer locks, and #580/PWQ-41/#1089 release serialisation remain independently owned. Nothing is deployed or dispatched by this change.

Hardening extension: job permission aliases (`*alias`), anchors (`&name`), tagged values (`!!str`) and quoted permission values are conservatively denied whenever `workflow_run` is present; these forms cannot be treated as independently verified read-only policy.

The hosted runner also resolves both canonical `main` and the exact pull-request head remotely **before checkout and after the tests**, matching immutable event SHAs. Changed refs fail instead of providing stale-green source evidence; this is only a read-only evidence check, not permission to merge.
