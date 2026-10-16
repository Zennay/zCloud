# Privileged cache writer no-new-debt check (16 October 2026)

Owner: independent deploy-ops source-only lane. Parent issue: #633. Notion posture: **Accelerate**.

## Prevented regression
A newly added or relocated direct `actions/cache@` / `actions/cache/save@` step in a workflow triggered by `pull_request_target`, `workflow_run`, `issue_comment` or `repository_dispatch` now fails a GitHub-hosted PR source gate. `actions/cache/restore@` is read-only and is not counted. Cache keys, paths and save-always inputs form part of the fingerprint; replacing an existing cache writer cannot silently consume its allowance. YAML aliases are resolved by the parser, duplicate keys are refused, and scripts reject ambiguous Git trees, symlink/gitlink workflow manifests, oversized blobs, moving main, and noncanonical Git remotes.

The workflow executes on GitHub-hosted `ubuntu-latest` only; it has `contents: read`, immutable SHA checkout, disabled persisted credentials and no deploy privileges. Its scanner compares complete immutable Git trees, not only the files listed in the PR payload. Baseline findings remain visible and do **not** become remediated merely because a new candidate is clean.

## Important boundaries
This guard covers **direct cache writer steps in privileged workflow manifests only**. It does **not** recursively resolve repository-local or remote composite actions or custom scripts writing cache through REST. It is not proof that every existing cache step is safe; pre-existing findings are retained and require separate review. It does not inspect actual cache contents or assert a cache-poisoning incident. With branch protection currently off on `main`, a successful hosted job is source evidence, not an enforced merge or production gate.

No change to permanent VPS listeners, services, Firefox, SQLite, workers, queue, production deploy, branch protection or any other owner's existing files. Exact HEAD CI, independent code review and separate release approval remain necessary before merge/deploy.

## Local test command
```sh
python3 -m pip install 'PyYAML==6.0.3'
python3 -m unittest -v tests.test_zcloud_privileged_cache_write_ratchet_20261016
python3 scripts/zcloud_privileged_cache_write_ratchet_20261016.py \
  --base <immutable-main-sha> --head <immutable-PR-sha> --require-current-main
```
