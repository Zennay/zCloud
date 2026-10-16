# Deploy-ops: workflow_run recursive redispatch ratchet (16 Oct 2026)

**Scope:** exclusively additive offline source admission for `workflow_run` jobs that invoke a second workflow with `gh workflow run`, `gh run rerun`, a POST to `/dispatches`, or GitHub SDK dispatch methods. This addresses a separate event family from scheduled redispatch PR #1502. Tracked against #633 and #1216 without taking over either active owner.

- Candidate must descend from the immutable exact canonical main SHA. The checker reads exact Git tree object IDs, not mutable working-tree YAML, and declines symbolic links, gitlinks, oversized or undecodable manifest objects.
- It compares source findings by GitHub workflow path. Identical existing findings are grandfathered as **legacy debt, not approved risk**. A newly introduced recursive dispatch finding fails admission.
- Run blocks are inspected; comments outside executable shell are not treated as commands. Static detection is deliberately non-exhaustive: dynamically constructed commands and helper scripts require manual review. Do not infer that PASS authorizes merge or production.
- This branch only executes a GitHub-hosted, read-only, pinned-checkout `push` validation against its own immutable SHA and independently observed main. It does not use `pull_request` or `pull_request_target`, does not create new self-hosted jobs, and has no write permission.
- No merges, VPS dispatches, process restarts, queue/SQLite/Firefox/worker changes, direct-main changes, or production release authority. Live runner gap #1326, writer-lock collision #1447 and main protection #922 remain their separate owners.

Validation: `python3 -m unittest -v tests.test_zcloud_workflow_run_dispatch_gate_20261016_w34` then `python3 scripts/zcloud_workflow_run_dispatch_gate_20261016_w34.py --base <exact-main-sha> --head <exact-head-sha>`. Return codes: 0 source-only no-new-debt, 1 new risk, 2 missing/unverifiable evidence. PASS **is not evidence of production readiness**.
