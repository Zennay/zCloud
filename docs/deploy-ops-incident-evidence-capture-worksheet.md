# Deploy-ops incident evidence capture (operator worksheet)

This worksheet records observations for an incident without acting on production. It is intentionally **not** a deployment plan, approval form, release gate, rollback instruction, or attestation. No CLI command in this document changes runtime state. File-disjunct from the serialized-gate code and receipt validators owned by other PRs.

## Identity and time
- Incident identifier:
- Recorder and observation time (UTC, ISO-8601):
- Window beginning / end (UTC):
- Repository and canonical main **40-character commit SHA**:
- Candidate PR number / branch / **40-character head SHA**:
- Observed deployment SHA (or `unknown`; never infer from a green run):
- Production URL(s) observed (redact credentials):

## Immutable pointers to observed evidence
Record each source with its UTC capture time, link, observed run/commit identity, result, and known blind spots. Distinguish **source observations** from **operator interpretations**.

| Source | URL or artifact hash | Observed SHA / run ID | Captured UTC | Observed result | Limitations |
| --- | --- | --- | --- | --- | --- |
| Exact-head regression | | | | | |
| Permanent VPS runner identity | | | | | |
| Serialized owner inventory (#580, #1089 and other live blockers) | | | | | |
| Production deployment workflow | | | | | |
| Independent external health check | | | | | |
| Post-deploy receipt | | | | | |

## Sequence of events
Record in chronological order. For each entry, capture **UTC timestamp**, **actor**, **source**, **event**, and **impact**. If the source is incomplete or contradicted, mark the discrepancy and preserve both observations; never rewrite history to fit the newest result.

## Safety and ownership boundary
- Current active production/change owner(s) and last recheck UTC:
- Ownership conflicts, missing information, or active workflow:
- Evidence invalidated by main/head change, rerun, cancellation, owner change, workflow configuration change, or runner drift:
- Operational impact and affected service(s):
- Security/privacy redactions applied (never paste access tokens, cookies or user data):

## Operator decisions (documentation only)
- Investigation outcome: `undetermined` / `no change` / `escalated`
- Evidence limitations:
- Human reviewer and decision time:
- Follow-up issue or PR:
- Separate change authorization reference, if any (this worksheet cannot supply one):

**Non-authorizing defaults:**
- `release_authorized=false`
- `merge_authorized=false`
- `deploy_authorized=false`
- `rollback_authorized=false`
- `mutation_performed=false`

Any missing, stale, contradictory or SHA-unbound evidence means **no authorization is inferred**. A successful GitHub Actions run is neither proof of externally healthy production nor permission to restart, rollback, deploy, merge, modify queue state or seize another worker's ownership. For a real production incident, escalate to the named active owner and require explicit independent approval and fresh evidence under the existing serialized gate.
