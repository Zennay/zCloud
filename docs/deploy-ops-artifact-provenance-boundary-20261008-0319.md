# Deploy artifact provenance acceptance contract (offline reviewer)

**Status: NOT ADMITTED.** This document is advisory and does not grant deploy, recovery, merge, metadata mutation or runner permissions. It complements the independently owned artifact URL screen (#1155), archive member path screen (#1156), evidence JSON strictness (#1152), and dashboard recovery fix (#1135), without modifying their files.

## Required provenance tuple
Before an artifact could ever contribute to a release decision, an independent reviewer must bind all of the following to one immutable tuple:

- GitHub repository *numeric ID* and exact expected owner/name, not a repository display string alone.
- Workflow ID and workflow path from a trusted default branch.
- Run ID, run attempt number, event type and trusted actor.
- Exact main commit SHA, the checked-out commit SHA and immutable artifact digest.
- Artifact ID and creation timestamp, expiration timestamp, size and content type.
- The verified source of the digest (not a value taken from the candidate artifact itself).
- The serialized admission decision for #580/PWQ-41 and #1089, as fresh independent evidence.

An omitted, malformed, conflicting or stale field means **UNKNOWN / DENY**, even when other checks pass. No matching tuple by itself authorizes a release.

## Offline negative acceptance cases

| Case | Input condition | Required outcome |
| --- | --- | --- |
| P01 | Artifact URL belongs to a trusted host but redirects to a different origin | Deny until redirect chain is explicitly verified by separate trusted logic |
| P02 | Artifact ID valid, but run attempt changed after a rerun | Deny old evidence |
| P03 | Same artifact name used by two run IDs | Deny name-only binding |
| P04 | Repository name matches but numeric repository ID differs | Deny |
| P05 | SHA matches but event is untrusted pull_request | Deny |
| P06 | Artifact digest originates from the downloaded artifact's own manifest | Deny self-attestation |
| P07 | Workflow ID or workflow file changed at a later commit | Deny mismatched workflow provenance |
| P08 | Artifact expired or timestamp unavailable | Deny |
| P09 | Clock timestamp is in the future beyond accepted skew, or time source unknown | Deny |
| P10 | Digest algorithm absent, unsupported, or ambiguous | Deny |
| P11 | Evidence JSON passes parsing but references a different run attempt | Deny |
| P12 | Both serialized gates are open, stale, or unknown | Deny deployment regardless of artifact integrity |
| P13 | Successful external verification and failing privileged recovery are conflated | Deny recovery/deploy admission |
| P14 | Digest and all provenance fields match, but live production receipt is missing | Deny production completion claim |

## Strict non-authorizing reviewer output

Every implementation consuming this document should emit `deploy_authorized=false`, `recovery_authorized=false`, `merge_authorized=false`, `mutation_performed=false` regardless of offline screening result. Use only redacted identifiers and failure codes in evidence output; never log token, URL query string, credentials, or raw artifacts.

## Execution and ownership

This is a new documentation-only scope; there is **no** archive download, parsing, GitHub workflow dispatch, runner use, VPS repair or production change. Acceptance requires explicit ownership review against all contemporary deploy-ops PRs, exact-final-head regression, and a separate trusted release admission with independent authorized operators. This matrix is not that admission.
