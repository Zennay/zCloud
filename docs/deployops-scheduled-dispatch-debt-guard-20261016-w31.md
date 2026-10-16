# Scheduled redispatch growth guard — 16 October 2026

Deploy-ops source-only prevention associated with [#633](https://github.com/Zennay/zCloud/issues/633) and known repeat-dispatch debt [#1216](https://github.com/Zennay/zCloud/issues/1216).

A scheduled workflow that calls `gh workflow run`, the REST `/actions/workflows/.../dispatches` endpoint, or Octokit workflow-dispatch methods can flood CI or repeatedly rearm a failing deployment. This guard computes dispatch-site counts from immutable Git blobs on the exact PR base and head. It rejects **new** sites in scheduled workflows while exposing legacy debt without silently claiming it is fixed. A new scheduled trigger on an existing manual dispatch workflow is also considered new debt.

Security bounds: Git SHA is a full 40-digit lowercase object identity; manifests must be regular files; tree records and blob lengths are validated, inventory count, per-file bytes, aggregate bytes and each Git subprocess have hard limits. Source is never interpolated into logs; no credential or API write permission is requested. Only the GitHub-hosted PR job evaluates candidates.

Validation: `python3 -m unittest -v tests.test_zcloud_scheduled_dispatch_debt_guard_20261016_w31` and `python3 scripts/zcloud_scheduled_dispatch_debt_guard_20261016_w31.py --base <EXACT_BASE_SHA> --candidate <EXACT_HEAD_SHA>`. Nonzero exit means reject or incomplete evidence.

Not covered: indirect shell wrappers, multiline-disguised dispatch commands, imported reusable workflow behavior, runtime state and existing #1216 release workflow debt. Passing this test **does not** authorize a deploy, prove runner health, close #1216, or replace serialized gate #580/PWQ-41 / #1089. Actual idempotent recovery repair is exclusively owned by #913 and the existing policy work [#1239](https://github.com/Zennay/zCloud/pull/1239). No direct edit, rearm or dispatch of the live recovery workflow is permitted by this standalone PR.
