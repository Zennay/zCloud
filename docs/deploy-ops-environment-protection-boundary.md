# Deploy-ops protected environment approval boundary

This is an offline non-authorizing operator acceptance checklist. It does not change workflow, environment, runner, service, VPS or queue state.

## Evidence requirements

1. Resolve protected environment name and repository identity from the actual deployment job, not a PR description.
2. Bind run ID, run attempt, job ID, workflow path, event name, actor, exact head SHA and environment name in one evidence envelope. Missing or conflicting values mean DENY.
3. An environment approval admits only that particular protected job under configured rules. It is not proof of full regression, permanent runner identity, serialization, rollback readiness, exact checkout or post-deploy health.
4. A re-run creates a distinct attempt. Never reuse an approval from an earlier attempt as evidence for a later attempt.
5. A pending, waiting, skipped, cancelled or timed-out job is not deploy-success evidence; API errors, incomplete pagination or unknown statuses also mean DENY.
6. Independently capture approval, job admission, VPS mutation receipt, external health receipt and release closeout. Never collapse them into one green status.
7. Environment protection bypass or policy changes require independent review and an audit reference; do not expose secrets in diagnostics.

## Explicit denial cases

| Observation | Decision |
| --- | --- |
| Production approval from another run attempt | DENY |
| Staging approved but production targeted | DENY |
| Approval valid but candidate SHA mismatch | DENY |
| Approval valid but full regression red or absent | DENY |
| Approval valid but serialized release owner occupied | DENY |
| Deployment job green but external health missing | DENY release closure |
| API result incomplete or errored | DENY |

Record only sanitized evidence identifiers, timestamp, environment, run attempt/job ID, exact SHA, owner gate, runner attestation and receipts. Documentation-only inspection always has release_authorized=false, deploy_authorized=false and mutation_performed=false.

This note grants no merge or deploy permission. The existing serialized #580/PWQ-41 and #1089 production owners (or accepted successors) retain control.