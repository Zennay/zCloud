# Foreign checkout origin: hosted-only, no-new-debt guard (16 October 2026)

**Scope:** zCloud issue #633; independent from the serialized #580/PWQ-41/#1089 production release, runner #1326, manual VPS writer lock #1447, and issue-first triage PR #1498.

## Why

A GitHub Actions step using actions/checkout can explicitly set with.repository to an unexpected or PR-controlled repository. Running a subsequent job against that source on a privileged/self-hosted runner can silently change the origin of code being executed. The existing checkout token and action pinning guards do not establish this same-repository origin contract.

## What this changes

- Add a read-only, GitHub-hosted PR job, limited to workflow, composite action and guard changes.
- Admit only first-party PRs targeting main, verify exact immutable Git HEAD, and read both exact SHA trees.
- Inventory every .github/workflows/*.yml|yaml and every tracked root or nested action.yml|yaml (including composite actions outside .github/actions) at base and head. Trigger the hosted source gate on additions or edits in any action manifest directory. Refuse partial inventories. Validate each Git-tree record's object ID, blob type and ordinary file mode (100644 or 100755); reject symlink manifests (120000), gitlinks (160000), malformed tree records and duplicate manifest paths before parsing. Read immutable object IDs rather than path-addressed blobs. Check each Git blob size **before** reading it; enforce 1 MiB per manifest, 16 MiB cumulative inventory, 4096 manifest maximum, and an exact content-size match. Oversize or inconsistent source produces a nonzero indeterminate result rather than a partial allow.
- Detect explicit foreign or dynamic repository overrides on actions/checkout steps even when YAML mapping keys put `with.repository` before `uses`, duplicate overrides, aliased/computed checkout input maps, indirect or aliased uses-actions and whole-step imports, and unrecognised checkout syntax.
- Compare per-path multiset counts. Existing debt is grandfathered, while new copies fail.
- Exit 0 = no new risk, 1 = introduced risk, 2 = indeterminate. JSON includes exact SHA pair and reasons.
- Run 86 offline regression tests against safe/risky inputs and a synthetic two-commit repository.

## Safety and limits

This is a conservative source-level heuristic, **not** a YAML-spec-complete parser or a trusted attestation of executable source. YAML aliases are now denied on checkout input maps, step-level uses action identities, and whole-step alias/merge imports. This conservatively rejects new indirect references even for actions other than checkout, since they can conceal checkout origin. Dynamically generated jobs, complex YAML merge semantics, reusable workflows and checkout post-processing still require separate scrutiny. A same-repository checkout override is not itself proof of an immutable trusted revision. PR-head code can weaken its own advisory check; independent code review and existing admission gates remain required.

CI runs on a GitHub-hosted runner with contents: read, not the VPS. No VPS/Firefox/systemd/SQLite/worker/queue changes, no runner job cancellation, no write token or production deployment.


## Foreign Git submodule source expansion

An explicit `actions/checkout` on the canonical zCloud repository can still retrieve
unreviewed third-party Git source when `with.submodules` is `true`, `recursive`, or
PR-controlled. The monotone source ratchet now rejects **new** enabled, dynamic or
ambiguous checkout submodule inputs. Only a literal `false` (including quoted and
inline-flow-map values) is admitted. The parser attributes submodule settings
**only** to the checkout step's `with:` mapping, not a coincidentally named
environment variable, step title or arbitrary scalar. Duplicate `with:` maps
within the same checkout step fail closed as ambiguous YAML. Existing risky submodule uses remain visible
as grandfathered debt; new copies of them fail. This source-only protection does
not inspect existing `.gitmodules` contents or enable unattended production use.

## Checkout mapping-key order protection

YAML step keys may appear in any order. The scanner now walks the whole enclosing YAML step from its first `- name`, `- with` or `- uses` line through the next step; it does not start at `uses:`. Adversarial tests prove both `repository` and aliased `with` before `uses` are denied, while unrelated previous action steps remain isolated.

## Git tree identity and symlink protection

The tree census parses Git `ls-tree -r -z` metadata instead of `--name-only`. A symlinked workflow/action manifest is **not** a safe YAML source even if the link target looks harmless in the blob content; the scanner now refuses it, including previously present symlinks, until an owner removes the ambiguity. Exact object-ID-based reads also avoid path-dependent blob resolution. The suite contains real temporary Git-repository symlink tests, mocked gitlink, duplicate path and malformed record tests. This remains advisory and never authorizes a deployment.

## Source-workflow syntax regression

The hosted workflow includes an offline contract test requiring five distinct steps and disallowing duplicate top-level step keys. This protects against silently malformed YAML during future guard edits; GitHub's own workflow parser must also accept the final source before the run counts as green.

## Canonical-main freshness admission

Before executing any PR-head Python, the hosted workflow independently checks the HTTPS origin and exact current-main SHA in an admission shell step. The source scanner then independently re-verifies main around its own inventory; it does not rely solely on the PR-head script to admit execution.

The hosted PR check additionally verifies that the git origin URL is exactly the public HTTPS
`https://github.com/Zennay/zCloud[.git]` and obtains only the advertised
`refs/heads/main` SHA with redirects disabled. The advertised main SHA must equal
the event-bound base commit immediately before scanning and again after reading both Git
trees. Unknown remotes, changed main, malformed/multiple advertisements, Git failures
and 20-second subprocess timeouts produce a nonzero indeterminate result, not green proof.
The second check closes the source-scan race; it does not provide atomic GitHub approval
or resolve unrelated serialized production release gates. `--require-current-main` is
explicitly opt-in so offline local contract fixtures do not access GitHub.

## Manual exact-SHA check

Run:

python3 -m unittest -v tests.test_zcloud_foreign_checkout_ratchet_20261016

python3 scripts/zcloud_foreign_checkout_ratchet_20261016.py --base <40-hex-base-sha> --head <40-hex-pr-head-sha>

Review JSON new_findings plus the GitHub-hosted workflow run for the identical immutable head before independent review.
