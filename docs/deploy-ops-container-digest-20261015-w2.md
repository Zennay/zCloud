# Container image digest no-new-debt guard — 15 October 2026

## Problem and scope

Actions and checkout pins alone do not fix a mutable `container: alpine:latest`,
`services: redis:7`, `image: ...` or `uses: docker://image:tag` reference.
A later third-party image release can alter the code used during an apparently
identical workflow run. This is independent of zCloud's existing Actions pin,
untrusted-shell interpolation, PR-target and VPS runner ownership guards.

This new **GitHub-hosted, source-only advisory gate** compares two **immutable**
Git commit trees and rejects newly introduced unpinned image references.
Legacy unpinned references remain inventoried, not automatically deleted or
considered production-approved. Changed legacy reference values become new
findings. The workflow targets same-repository owner PRs only and has
`contents: read` permission. No self-hosted runner and no production dispatch.

## Admission

- Compare Git tree `--base-sha` and `--head-sha` exactly; only 40-character
  hexadecimal SHA values are valid, with bounded object and report sizes.
- GitHub Actions checkout uses the immutable PR head, disabled persisted
  credentials, and requires canonical main SHA to agree with the PR's base.
  Main is checked again after the read-only comparison. The current
  PR source branch is likewise checked at both boundaries: its remote
  commit must still equal the checkout's immutable expected SHA before
  executing scanner code and after the final evidence is emitted. This
  two-point check is not an atomic source lock or merge authorization.
- Require `@sha256:<64 hex>` to pin new literal container images, service
  images and `docker://` references. YAML alias, dynamic GitHub expressions,
  multiline image forms, explicit YAML mappings, encoded double-quoted YAML
  keys, indirect `uses: *alias` / dynamic Action references, and ambiguous
  flow-style map/`docker://` steps require independent review and are refused
  as *new* risk. A `uses: docker://` value must consume the **entire** YAML
  scalar, with no unexamined suffix; single- and double-quoted image values
  require balanced quotes rather than stripping arbitrary quote characters.
- Symlinked workflow/composite metadata, malformed UTF-8, missing Git objects
  or oversized scans **fail closed** rather than establishing false safety.
  Root and arbitrarily nested \`action.yml\` / \`action.yaml\` metadata are
  included, not just \`.github/actions/**\`. The PR gate also triggers on those
  paths at any directory depth.
- Whole-line YAML flow sequences such as `steps: [{uses: docker://alpine:latest}]`
  and compact jobs like `jobs: {build: {container: alpine:latest}}` are
  treated as ambiguous source, even if a candidate string includes a digest.
  Encoded flow mapping keys (for example a quoted `\\u0075ses` spelling)
  also deny by default. This is intentionally conservative: it closes gaps
  in block-key-only scanning without claiming a complete YAML parser.
- Never place image scalar values, bearer tokens or shell bodies in output.
  Reports contain bounded paths, line numbers and categories only.

## Legacy risk relocation and opaque Git trees

Old unpinned references remain tolerated **only when the complete committed
workflow/Action metadata file is byte-identical**. Moving a legacy tagged image
to another job, changing its runner, permissions, triggers, or even editing
unrelated text in the same risky file requires review and fails the ratchet.
This deliberately prefers false positives over inadvertently treating a
production privilege escalation as unchanged legacy risk.

Local Actions may be invoked from arbitrary repository directories, and a Git
symlink or gitlink can hide their actual executable metadata. The bounded
Git-tree scan therefore fails closed for **any** symlink or submodule entry
instead of assuming that arbitrary aliases are harmless. Accordingly the
GitHub-hosted PR proof deliberately **has no path filter**: any changed
file could become an opaque local-Action alias. A name-restricted path
trigger would otherwise silently skip the scanner on exactly that PR.
No production service, Git branch protection or local checkout is mutated
by this scan.

## Hosted CI operator summary

Every source check writes a Dutch GitHub Actions step summary, including
**rejected** results. Successful comparisons include bounded integer counts
for historical, candidate and newly introduced image risks. New image debt
produces an explicit red denial with counts. Incomplete or invalid Git evidence
produces an explicit red "bronbewijs incompleet" message; even the scanner's
original error string is omitted. The summary never includes image values,
paths, repository secrets, branch names or shell scripts. The workflow
preserves the scanner's failing exit code and rejects contradictory or
forged-authority JSON rather than reporting an optimistic success. Numeric
counts must also agree with the bounded `new_risks` list (maximum 25 detail
rows) and may never claim more new findings than candidate findings.
The summary is observation evidence only: it does not open a release gate,
prove a healthy VPS listener or authorize mutation.

## Explicit limits

This is a conservative syntactic risk tripwire, **not** a full YAML 1.2 or
GitHub expression interpreter, not a vulnerability exploit proof, and not
supply-chain signature or image registry attestation. It does not fetch or
execute container images, resolve digests, or establish whether a workflow's
container is actually used at runtime. A source-only green check is not
evidence of a working zCloud VPS listener.

Release ownership remains with #580/PWQ-41, #1089 and #1447; runner incident
#1326 is separate. PR review and current-main protected release approval
are required before any production mutation. No merge/deploy/restart/Firefox/
SQLite/queue/worker/runner action is authorized by this report.
