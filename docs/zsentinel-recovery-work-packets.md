# zSentinel recovery work packets

Source-only delivery selector for the zSentinel cross-project recovery ROLE, not a new product, QA lane, scheduler or merge authorization.

## Run

    python3 scripts/zsentinel_work_packets.py /trusted/receipt.json --limit 4
    python3 -m unittest tests.test_zsentinel_work_packets -v

The verified JSON receipt must be a regular UTF-8 file <= 256 KiB, with unique JSON keys, recent observation (15 minutes, 2 minutes future tolerance), and separately verified GitHub/Notion/lease facts.

Required envelope keys: schema_version (integer 1), observed_at (UTC ISO timestamp ending Z), projects, items.

Each project has id (canonical slug), main_sha (40 lowercase hexadecimal SHA) and lease_verified (real boolean).

Each item has project, item_id, kind (issue or pr), priority (P0-P3), impact and effort (integers 1-5), owner and claim (free/active/unknown), hold (none/external/release), source_allowed (boolean), and files (1-12 safe relative source paths). An existing PR additionally requires its immutable head_sha/base_sha, ci_passed, review_approved, mergeable and draft booleans. Its base_sha must equal the separately observed current main SHA.

## Delivery semantics

1. Reject malformed, stale, ambiguous, unsafe or unbounded evidence before selecting any work.
2. Reject active owner/claim collisions and unknown or unverified project leases. An in-band permission flag is not authentication.
3. Choose P0/P1 high-impact work first and prioritize already-reviewed, green, mergeable, non-draft existing PRs over launching more QA-only branches.
4. A human/device/research rights HOLD blocks integration but may permit independent source-only issue implementation when separately authorized. A release gate remains intact.
5. Return at most four non-overlapping packets, at most two per project. Every packet requires real implementation, focused plus mandatory tests and an exact-head authorized integration review. The selector neither performs nor attests to those steps.

The output excludes untrusted titles, credentials and prompts, and always declares mutation_performed=false, queue_claimed=false and merge_or_deploy_authorized=false.

## Integration gates

This CLI is not yet wired into live zCloud worker dispatch. The central control-plane owner must first review the integration against active queue leases, role prompts, repository ownership and existing protected server.py. The VPS checkout has uncommitted local edits: never overwrite it blindly. Exact-head CI and serialized release approval are separate requirements; source green does not mean the VPS runner or workers recovered.

Track source fixed, CI passed, PR merged, deployed, runtime recovered, worker resumed, and external holds separately. See zSentinel PWQ-382 and zCloud issue #1326.

## Product-only zSentinel packets (17 October 2026 user instruction)

The otherwise backward-compatible item schema now accepts an **optional**
`work_type` field with values `product`, `qa-only`, or `review-only`.
A separately source-verified QA-only or review-only ticket is **not selected**
as a new packet; `blocked.qa_or_review_only` explains the skip. Existing
`owner`, `claim`, `lease_verified`, reviewer, main-SHA and CI gates remain
unchanged, and the CLI still does not create a real queue claim.

A missing `work_type` on older receipts is displayed as
`legacy_unclassified`, not falsely inferred as verified product delivery.
The source collector should populate `work_type=product` only when the
underlying issue/PR is genuinely a material feature or bugfix. This module
never guesses classification from an issue ID or title.

Each selected packet's delivery list now requires real implementation,
**brief build/existing CI inspection rather than writing or running new QA
suites**, and exact-head authorized review before any integration. Existing
mandatory GitHub CI, legal/security/FTMO permissions and release gates are
never bypassed. This is a source-only CLI improvement; it is not connected
to or permission for live worker dispatch.
