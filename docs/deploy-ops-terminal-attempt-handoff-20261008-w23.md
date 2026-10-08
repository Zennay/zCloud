# Terminal GitHub Actions evidence: offline deploy-ops handoff

This document is a non-authorizing evidence checklist associated with draft PR #1203. It does **not** grant a recovery, deployment, merge, approval, or workflow dispatch. Canonical release ownership is #580/PWQ-41 plus #1089.

## Trusted evidence required before a release decision

1. Re-read the current canonical `main` ref, the candidate head, repository ID, workflow run ID and run attempt **before and after** API evidence collection; deny on drift or incomplete pagination.
2. Bind all job conclusions to that immutable run-attempt-head triple; reject a job whose record is incomplete, duplicated, untrusted, or still in progress. A terminal `success` is only a fact about a job, not a deployment permit.
3. Capture and correlate the complete set of required job IDs against the trusted workflow definition. The offline helper deliberately does **not** determine required jobs, job provenance, whether a job was skipped legitimately, or whether a successful job covered current code.
4. Independently require exact-head regression and security tests, an authorized environment and human/owner production window, rollback readiness, and externally observed health. An internal 503 alone does not warrant a restart.
5. Keep secrets out of logged evidence. Record final SHA, approval origin, timestamps, run attempt, redacted receipts and reviewer sign-off.
6. If any input is missing, ambiguous, contradictory or stale, do not mutate the VPS, service, SQLite, browser, workflows or queue.

## Offline contract acceptance

- Wrong repository or source identity must be denied by the **caller**; this helper has no repository-bound input.
- Wrong run, attempt or SHA must be rejected by the helper.
- Missing job IDs, unknown terminal states, duplicate records or malformed UTC timestamps must be rejected.
- Nonterminal jobs must never be upgraded to a successful conclusion.
- `live_deploy_authorized` is intentionally always false, including for syntactically valid success fixtures.
- Running these fixtures is not a production proof. Required follow-up: exact-head hosted test, full regression, complete owner-branch inventory and serialized gate review.

## Distinct responsibilities

The helper `scripts/zcloud_deploy_attempt_conclusion_offline_20261008_w23.py` validates **record shape**, run-attempt binding and uniqueness only. It does not query GitHub, select required jobs, verify GitHub API authenticity, assess workflow trust, inspect environment protections or execute rollback. In particular it does not replace #1135 recovery workflow work, #581 health fast-path, #1108 planner checks, or #1197/#1200 SHA/status evidence.

No deployment is allowed because this document exists.
