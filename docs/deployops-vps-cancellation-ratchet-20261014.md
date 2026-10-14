# zCloud deploy-ops: VPS cancellation-source ratchet (14 October 2026)

**Source-only.** This is a bounded extension of [#633](https://github.com/Zennay/zCloud/issues/633) / [#1193](https://github.com/Zennay/zCloud/issues/1193), not a replacement for ongoing workflow owners or production admission.

## Failure prevented

GitHub Actions `concurrency.cancel-in-progress` can terminate an already-running job sharing the same concurrency group. A privileged or merely read-only self-hosted diagnostic may disrupt ongoing work if a second run starts with a matching group. A manual workflow trigger alone is not sufficient protection.

The new `scripts/zcloud_deployops_cancellation_ratchet_20261014.py` compares two **exact 40-hex Git revisions**, scans only changed workflow blobs, and denies a changed workflow that combines a possible self-hosted or dynamically selected runner with effective/ambiguous global or job-level cancellation. Reusable-workflow delegation is treated as possible self-hosted execution. Unknown layouts, symlinks, oversized/undecodable files, and ambiguous Git source are denied.

An older workflow revision can still have a live VPS job when the new revision starts. The gate therefore also refuses **switching from a VPS-capable previous revision to hosted-only jobs while enabling cancellation**, including job-level concurrency. A string value like `'false'` or `"false"` is not proof of disabled cancellation; only the exact unquoted YAML boolean `false` is admitted as non-cancelling.

Existing untouched workflows are not changed or declared secure; edited legacy cancellation debt requires owner review. Removing cancellation risk is allowed.

## Cross-workflow and cross-revision safety

The check now looks at **both the previous and candidate Git snapshots**. Switching a workflow to hosted-only jobs does **not** erase the possibility that a previous VPS job is still running under its old concurrency group. The gate denies newly enabled global or job-level cancellation in that case.

For edited hosted-only workflows with cancellation enabled and a statically known concurrency group, the gate additionally builds a single bounded read-only index of all old/new workflow files and denies collisions with other VPS-capable workflow paths sharing that literal group. Quoted/unquoted literal groups are normalized, duplicate groups are denied, and incomplete cross-workflow sources fail closed. Dynamic or missing group expressions are deliberately **denied for a changed hosted-only workflow that enables cancellation**, because group isolation cannot be proven. This is a conservative source-review requirement, not a claim that any particular dynamic group is colliding. This source comparison is not a substitute for GitHub's live queue/runner-state proof.

## Measured baseline

[Source-only hosted run 38617904818](https://github.com/Zennay/zCloud/actions/runs/38617904818) reported **216 workflows: 142 with no match for this limited source-risk rule, 74 matching possible cancellation risk, 0 unknown**, on its exact tested branch SHA. This is code-level potential exposure only, **not** 74 outages or 74 verified live runner cancellations. No workflows or queues were changed by the census. The previous/main baseline should be distinguished from any later candidate count by exact Git SHA, and the newer hosted workflow now prints both immutable snapshots.

## Verification

```bash
python3 -m unittest -v tests.test_zcloud_deployops_cancellation_ratchet_20261014
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py --base <exact-base-sha> --head <exact-head-sha>
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py --inventory-head <exact-head-sha>
```

The read-only inventory mode classifies every workflow in an exact Git snapshot as `safe` (only with respect to this **limited cancellation rule**), `risky` or `unknown` (incomplete/unparseable); it prints bounded aggregate counts only. `safe` does not imply overall workflow security, production safety or runner availability. It returns an observation rather than mutation authority.

The companion `.github/workflows/zcloud-deployops-cancellation-ratchet-20261014.yml` runs exclusively on **GitHub-hosted ubuntu-latest**, with `contents: read`, immutable checkout and `persist-credentials: false`. It includes a candidate-head check and full Git history for the comparison. It has no secret, VPS/runner, Actions write, force-start, worker/Firefox, SQLite or release permission.

## Automatic change coverage and actionable evidence

The hosted workflow checks **all changed `.github/workflows/**` sources on `main` pushes** as well as PR source changes; it previously only checked pushes touching its own policy files. This is a **post-push detection**, not a GitHub ruleset, an approval, or a pre-merge block. Do not treat its green status as proof that production was promoted, or its red status as authorization to cancel running actions.

The scanner now exposes a **bounded, path-only triage mode** (maximum 25 entries, 12 by default), with exact Git SHA in every inventory/triage summary. Entries distinguish `automatic_or_unknown`, `manual_only`, and `unknown_source`. Paths match the strict GitHub workflow filename grammar; raw YAML, runner labels, environment values, and tokens are never printed. Use this only as a starting shortlist, and inspect **fresh ownership in Notion and current open PR/branch changes** before taking any action.

```sh
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py \
  --triage-head <exact-commit-sha> --triage-limit 12
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py \
  --triage-head <exact-commit-sha> --triage-limit 12 --triage-prefix zcloud-
```

The optional `--triage-prefix` is strictly a **workflow filename prefix** (1–40 safe characters, no slashes), intended to limit follow-up to a disjoint project lane. This is not proof of unowned files: verify open branches, changed PR filenames and current Notion assignments before any edit. The generic top 12 currently includes FTMO-specific workflows, which this deploy-ops lane does **not** claim; the zCloud prefix exposes a separate 12-file shortlist. A path on this shortlist is a potential risk requiring investigation, **not** a remediation authorization.

When the old VPS workflow's concurrency group is dynamic, a new hosted cancellation group may resolve to the same name. The guard denies a claimed clean cross-workflow comparison when that previous VPS group cannot be established as distinct. A VPS-capable workflow with **no concurrency block at all** is not invented as a cross-group collision. The code also distinguishes literal quoted/unquoted group values.

## Known boundary

This conservative syntactic source gate is **not** a general YAML parser or proof of live runner state. It screens newly changed candidate workflows; it does not remediate legacy files, verify global GitHub concurrency-group uniqueness, prove queue safety, authorize a merge, or promote production. Before integrating, review file ownership, PR-triggered self-hosted workflows such as #638, privileged dashboard recovery #1135 and serialized #580/PWQ-41/#1089. Do not create a PR or restart jobs merely to obtain a green check while those live gates remain open.

## Reproduction evidence

- The regression suite now also covers cancelled previous-revision VPS jobs, exact-Git inventory cohorts, YAML quoted false values and malformed Git ancestry. CI is run on exact immutable heads; counts and run URLs must be verified against the final SHA, never copied from earlier heads.
- [Hosted exact-head cancellation ratchet #38615456281](https://github.com/Zennay/zCloud/actions/runs/38615456281): terminal success on SHA `90b3c81fa32d1c9a5f8f2177f3829e59297a0abe` (prior to this documentation-only commit).
- A full repository regression run must be checked against this document's final branch SHA; hosted source proof does not imply current VPS health.
