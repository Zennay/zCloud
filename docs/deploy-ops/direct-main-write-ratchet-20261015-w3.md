# zCloud deploy-ops: conservative direct-main-writer no-new-debt ratchet

**Owner:** parallel deploy-ops 1/2, independent branch \`deployops/direct-main-write-ratchet-20261015-w3\`.
**Tracking:** #922 (main protection compatibility), #633 (workflow trust debt). This branch **does not claim** the existing #1072/#1033 inventory owners or any writer, runner, deploy, rollback, queue, SQLite, browser or service workflow.

## Concrete capability

The immutable-blob checker \`scripts/zcloud_direct_main_write_ratchet_20261015_w3.py --base <40hex> --head <40hex>\` lists ordinary committed files below \`.github/workflows\` at the two Git commit identities and inspects **only added/modified workflow blobs**. Completely unchanged legacy workflow bytes are grandfathered without being re-certified. New or changed workflow bodies are denied when a line contains:

- direct \`git push\` (including \`git -C ... push\`, forced, any target branch or dynamic ref), conservatively including pushes to non-main branches;
- Git refs endpoint mutation strings involving common API clients, especially \`git/refs/heads/\`;
- dynamic \`gh api\` calls with visible mutating HTTP method selectors even when the ref endpoint is variable.

Changed blob symlinks, non-UTF-8/NUL, oversized source, missing workflow inventory and invalid commit IDs deny. Git replace refs and inherited \`GIT_*\` configuration are ignored when reading committed evidence. Each JSON receipt declares \`authorizes_merge=false\`, \`authorizes_deploy=false\` and \`mutation_performed=false\`. The tool performs no network, API, SQLite, environment or runtime writes.

The companion hosted-only PR job first checks canonical main and checked-out PR head against GitHub event SHA, runs actual temporary-Git adversarial tests and the immutable source checker, then rechecks both SHAs. It requests \`contents: read\`, does not persist checkout credentials, and never targets a self-hosted runner.

## Known limits and remaining prerequisites

This is a **conservative lexical check, not a complete YAML/shell/PowerShell interpreter**. Multi-line concatenation, shell aliases, alternate Git implementation wrappers, encoded commands and hidden API endpoints can evade lexical matching; shell examples inside executable \`run\` bodies can cause false positives. It excludes scripts external to \`.github/workflows\` and deleted workflow files, and cannot classify whether an existing writer is safe to retire. The receipt deliberately states the number of unchanged legacy blobs not re-scanned.

A PR author can alter their own PR source and tests. Consequently **a green PR-controlled gate is not independent trusted branch protection**. Do not mark #922 complete or merge on this evidence alone. Enforce independent review/protected required checks only after #1033 compatibility and existing workflow owners clear direct-main writers. Actual release additionally requires #580/PWQ-41, #1089, production writer locks #1447 and runner/source acceptance #1326.

The change is strictly additive and review-only; **no privileged workflow dispatch, live deployment, GitHub branch-rule modification, VPS/Firefox/service/SQLite/queue/runner/worker mutation** is authorized.
