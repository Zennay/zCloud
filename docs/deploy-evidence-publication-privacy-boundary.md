# Deploy-ops evidence privacy boundary

This is a review-only, non-authorizing contract for deploy-ops evidence publication. It does not replace release admission, exact-head CI, the serialized production owners (#580/PWQ-41 and #1089), or live post-deploy verification.

## Trust boundaries

1. **Untrusted producer:** workflow logs, runner diagnostics, HTTP response bodies, environment/config output, and operator-supplied receipt JSON may contain credentials, internal endpoints, filesystem paths and tenant data. Treat all as sensitive, even after a job succeeds.
2. **Bounded internal verification:** consume evidence only at an explicitly named, current commit SHA. A successful prior run cannot certify a newer SHA. Avoid logging raw receipts or the contents of config files.
3. **Public outputs:** PR comments, job summaries and uploaded artifacts are separately exposed surfaces. Publish only a reviewed allowlist of stable status fields; never dump an input object wholesale. GitHub secret masking is not a complete redaction control.
4. **Production authority:** no evidence object, summary field or offline checker output is an authorization token. In particular, `release_authorized`, `merge_authorized`, `deploy_authorized`, and `mutation_performed` must not be inferred from successful parsing or zero drift.

## Minimum safe public evidence allowlist

- Repository workflow run URL and run ID (not logs or artifact download tokens).
- Exact 40-hex candidate/deployed commit SHA, when verified against the active run and intended repository.
- Check name, concluded state, checked-at UTC timestamp.
- Boolean or enumerated gate result and a fixed, non-sensitive reason code.
- Counts of changed *field names* or failing checks, with bounded values; never raw field values.
- A static non-authorization footer: `release_authorized=false`, `merge_authorized=false`, `deploy_authorized=false`, `mutation_performed=false` for offline preflight publications.

**Never publish:** environment variables, auth headers, cookies, service tokens, private URLs, request/response bodies, DB paths, IP addresses, untracked filenames, stack traces with local paths, opaque receipt payloads, artifact URLs with signed query strings, or deployment rollback secrets. Do not rely on truncation alone as sanitization.

## Fail-closed review procedure

1. Identify the exact producing workflow and PR/head; check concurrent ownership before editing its files.
2. List every output sink: stdout, step summary, PR comments, action annotations and artifacts.
3. Check each emitted field against the allowlist above. Newly added fields default to **not publishable**, even when named `debug`, `metadata`, or `details`.
4. For fields requiring operational detail, keep the value in access-controlled evidence; publish only a constant reason code and a link to the authorized internal investigation path, without secrets in the URL.
5. Use negative fixtures with sentinel secrets, an internal hostname, a signed query token and a sensitive local filename. Assert none appears in public JSON, summaries, exception handling or failed-command output.
6. Verify no mutation path is exercised by these tests. Run focused regression and the full exact-head suite on the appropriate runner; wait for terminal results before marking a PR accepted.
7. Re-evaluate against current main and serialized deploy ownership before any merge or production action.

## Example safe operator result

```json
{
  "schema_version": 1,
  "check": "offline_deploy_evidence_privacy",
  "status": "needs_review",
  "reason_code": "UNREVIEWED_OUTPUT_FIELD",
  "changed_field_count": 1,
  "release_authorized": false,
  "merge_authorized": false,
  "deploy_authorized": false,
  "mutation_performed": false
}
```

This document makes no claim of enforcement in current workflows. Implement automated checks only as an isolated follow-up with its own owner, negative tests, exact-head proof and no changes to live deploy authority.
