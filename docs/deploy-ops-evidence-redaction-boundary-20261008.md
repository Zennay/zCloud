# Deploy-ops evidence redaction boundary

This checklist is a **non-authorizing** companion for incident handoff and audit evidence. It does not release the serialized control-plane gate, authorize a merge, or trigger deployment.

## Capture only necessary facts

- GitHub repository, PR number and immutable 40-character head/base commit identities.
- Named GitHub Actions workflow, run ID, terminal conclusion, and UTC timestamps.
- Canonical runner label set `[self-hosted, zcloud, vps]` and a pass/fail outcome for runner identity checks; never export machine credentials.
- A boolean summary of readiness and health checks, plus a link to access-controlled evidence.
- Current serialized gate owners and their actual disposition, verified afresh before any action.

## Never paste into PRs, Notion or incident comments

- API tokens, cookies, bearer headers, SSH keys or authentication environment variables.
- Full process command lines, live configuration files, request/response bodies, database rows, worker prompts, customer input, session storage or browser profiles.
- Unredacted journal lines, private runner diagnostics, signed artifact URLs or internal networking details.
- Complete exception dumps if they may contain the above; publish bounded, sanitized reason codes instead.

## Handoff procedure

1. Capture only immutable identifiers and terminal conclusions. Treat queued, cancelled and skipped as **not proven successful**.
2. Check links to evidence without expanding protected contents into a public issue or pull request.
3. Retain raw operational evidence in its existing access-controlled system under that system's retention policy. Do not create a second unbounded copy.
4. Publish a sanitized receipt identifying which checks were verified, when, and against which exact candidate SHA. Missing or contradictory evidence means *unverified*.
5. Before any subsequent promotion, re-check current main, candidate identity, runner proofs and the authoritative owner/blocker inventory. A historical receipt is never deploy authorization.
6. When evidence may have leaked credentials, stop redistribution, restrict access and follow the incident credential-rotation process; never copy the secret into the handoff.

## Non-authority declaration

```json
{
  "metadata_write_authorized": false,
  "merge_authorized": false,
  "deploy_authorized": false,
  "mutation_performed": false
}
```

This document is deliberately independent of the release runbook, receipt validator, gate owner files and all runtime/deploy workflows. It does not change the serialized #580/PWQ-41 + #1089 gate or the #1108 metadata-remediation owner.

## Sanitized receipt example (illustrative, not a live proof)

An operator can share a *minimal* handoff with immutable identifiers and outcomes. Never invent a success value when the underlying check is still running. This example is intentionally invalid as live authorization:

```json
{
  "candidate_sha": "0000000000000000000000000000000000000000",
  "base_sha": "0000000000000000000000000000000000000000",
  "checks": {
    "regression": "not_verified",
    "runner_identity": "not_verified",
    "external_health": "not_verified"
  },
  "serialized_gate": "not_verified",
  "metadata_write_authorized": false,
  "merge_authorized": false,
  "deploy_authorized": false,
  "mutation_performed": false
}
```

The all-zero identities are placeholders, **not** valid evidence. Replace them only with verified GitHub commit identities before publishing an operational receipt. Do not attach raw logs or include bearer tokens in evidence URLs. If a record cannot be safely redacted while preserving meaning, share only a restricted evidence reference and a bounded outcome code.

## Review acceptance

- The public handoff includes no secrets, signed URLs, personal data, raw log lines or machine-specific command lines.
- Each success claim is tied to a terminal completed check for the stated candidate SHA; in-progress, failed, skipped or missing checks remain unverified.
- A later main change invalidates the handoff as a fresh promotion basis until new exact-main acceptance is captured.
- Nothing in the handoff bypasses #580/PWQ-41 + #1089 or authorizes production mutation.
