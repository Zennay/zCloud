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
