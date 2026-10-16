# Foreign checkout origin: hosted-only, no-new-debt guard (16 October 2026)

**Scope:** zCloud issue #633; independent from the serialized #580/PWQ-41/#1089 production release, runner #1326, manual VPS writer lock #1447, and issue-first triage PR #1498.

## Why

A GitHub Actions step using actions/checkout can explicitly set with.repository to an unexpected or PR-controlled repository. Running a subsequent job against that source on a privileged/self-hosted runner can silently change the origin of code being executed. The existing checkout token and action pinning guards do not establish this same-repository origin contract.

## What this changes

- Add a read-only, GitHub-hosted PR job, limited to workflow, composite action and guard changes.
- Admit only first-party PRs targeting main, verify exact immutable Git HEAD, and read both exact SHA trees.
- Inventory every .github/workflows/*.yml|yaml and every tracked root or nested action.yml|yaml (including composite actions outside .github/actions) at base and head. Trigger the hosted source gate on additions or edits in any action manifest directory. Refuse partial inventories. Check each Git blob size **before** reading it; enforce 1 MiB per manifest, 16 MiB cumulative inventory, 4096 manifest maximum, and an exact content-size match. Oversize or inconsistent source produces a nonzero indeterminate result rather than a partial allow.
- Detect explicit foreign or dynamic repository overrides on actions/checkout steps, duplicate overrides, aliased/computed checkout input maps, indirect or aliased uses-actions and whole-step imports, and unrecognised checkout syntax.
- Compare per-path multiset counts. Existing debt is grandfathered, while new copies fail.
- Exit 0 = no new risk, 1 = introduced risk, 2 = indeterminate. JSON includes exact SHA pair and reasons.
- Run 39 offline regression tests against safe/risky inputs and a synthetic two-commit repository.

## Safety and limits

This is a conservative source-level heuristic, **not** a YAML-spec-complete parser or a trusted attestation of executable source. YAML aliases are now denied on checkout input maps, step-level uses action identities, and whole-step alias/merge imports. This conservatively rejects new indirect references even for actions other than checkout, since they can conceal checkout origin. Dynamically generated jobs, complex YAML merge semantics, reusable workflows and checkout post-processing still require separate scrutiny. A same-repository checkout override is not itself proof of an immutable trusted revision. PR-head code can weaken its own advisory check; independent code review and existing admission gates remain required.

CI runs on a GitHub-hosted runner with contents: read, not the VPS. No VPS/Firefox/systemd/SQLite/worker/queue changes, no runner job cancellation, no write token or production deployment.

## Manual exact-SHA check

Run:

python3 -m unittest -v tests.test_zcloud_foreign_checkout_ratchet_20261016

python3 scripts/zcloud_foreign_checkout_ratchet_20261016.py --base <40-hex-base-sha> --head <40-hex-pr-head-sha>

Review JSON new_findings plus the GitHub-hosted workflow run for the identical immutable head before independent review.
