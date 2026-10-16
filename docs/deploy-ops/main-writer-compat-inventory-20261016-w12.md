# zCloud main protection: historical writer compatibility inventory (16 October 2026)

Parent: [#922](https://github.com/Zennay/zCloud/issues/922). Related source-only prevention work: [PR #1471](https://github.com/Zennay/zCloud/pull/1471). This tool is different: it inventories the *existing* workflows at exactly one local main commit. It does not veto new changes, approve a protection rule, enforce runtime locks or perform a live operation.

## Usage

From a clean checkout whose HEAD and local refs/heads/main are the exact same verified commit:

    python3 scripts/zcloud_main_writer_compat_inventory_20261016_w12.py --expected-main-sha EXACT_40_CHAR_SHA --format markdown

For an evidence JSON with stable signal codes, use --format json. The output is read-only stdout; no files are written. The caller must establish remote GitHub origin/main identity independently before and after checkout; this helper verifies only the local immutable source identity. All error responses exit 2, are redacted and explicitly deny any production or governance permission. A successful inventory exits 0 **only** to indicate a completed *static* scan.

The scanner reads every tracked YAML workflow blob under .github/workflows at the pinned SHA. It recognizes direct pushes to main, other pushes, ref API calls, merge operations, write-token grants and GitHub script usage; reports filename, committed SHA-256 digest and line-numbered signal codes. Limits: 512 workflows, 512 KiB each, 24 MiB total; reject symlinked YAML, malformed/oversized/binary YAML blobs, unexpected paths, SHA drift and unexpected tree shapes.

## Why this does not solve #922 alone

- Static regex signals are candidates, **not proof of execution or active business need**. Scripts and imported composite actions may hide indirect writes; a no-signal workflow is not necessarily compatible.
- The inventory does not infer whether a job is obsolete, actively required or a false positive. For each candidate, the workflow owner must classify one of: active and migrate to PR/artifact/status/comment evidence; terminal and retire via its existing owner; false positive/read-only, with reason and verified provenance.
- Job trigger trust, privilege semantics, workflow provenance, recent PR-less branch ownership, runtime scheduler behavior and API writers outside workflows need separate scrutiny.
- A complete inventory does not certify independent review, configured main protection, required status checks or production readiness. Both safe_to_enable_branch_protection and production_mutation_authorized are **invariantly false**.
- Existing ownership of the writer workflows and governance work remains with #922/#1033/#580/PWQ-41/#1089 and their current PR owners. This PR introduces three uniquely named source/test/docs files; it must not alter those owners' files.

## Next owner-side acceptance

Run against a freshly verified canonical main SHA, retain the redacted JSON receipt, map every candidate to its existing PR owner and concrete disposition, then separately audit REST/GraphQL clients and composite actions. Re-run exact-head compatibility before proposing branch protection; use #1033's independent GitHub governance audit and the serialized release owner for any eventual live change. Never treat this scanner as an authorization gate.
