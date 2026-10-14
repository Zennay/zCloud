# zCloud deploy-ops: VPS cancellation-source ratchet (14 October 2026)

**Source-only.** This is a bounded extension of [#633](https://github.com/Zennay/zCloud/issues/633) / [#1193](https://github.com/Zennay/zCloud/issues/1193), not a replacement for ongoing workflow owners or production admission.

## Failure prevented

GitHub Actions `concurrency.cancel-in-progress` can terminate an already-running job sharing the same concurrency group. A privileged or merely read-only self-hosted diagnostic may disrupt ongoing work if a second run starts with a matching group. A manual workflow trigger alone is not sufficient protection.

The new `scripts/zcloud_deployops_cancellation_ratchet_20261014.py` compares two **exact 40-hex Git revisions**, scans only changed workflow blobs, and denies a changed workflow that combines a possible self-hosted or dynamically selected runner with effective/ambiguous global or job-level cancellation. Reusable-workflow delegation is treated as possible self-hosted execution. Unknown layouts, symlinks, oversized/undecodable files, and ambiguous Git source are denied.

An older workflow revision can still have a live VPS job when the new revision starts. The gate therefore also refuses **switching from a VPS-capable previous revision to hosted-only jobs while enabling cancellation**, including job-level concurrency. A string value like `'false'` or `"false"` is not proof of disabled cancellation; only the exact unquoted YAML boolean `false` is admitted as non-cancelling.

Existing untouched workflows are not changed or declared secure; edited legacy cancellation debt requires owner review. Removing cancellation risk is allowed.

## Verification

```bash
python3 -m unittest -v tests.test_zcloud_deployops_cancellation_ratchet_20261014
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py --base <exact-base-sha> --head <exact-head-sha>
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py --inventory-head <exact-head-sha>
```

The read-only inventory mode classifies every workflow in an exact Git snapshot as `safe` (only with respect to this **limited cancellation rule**), `risky` or `unknown` (incomplete/unparseable); it prints bounded aggregate counts only. `safe` does not imply overall workflow security, production safety or runner availability. It returns an observation rather than mutation authority.

The companion `.github/workflows/zcloud-deployops-cancellation-ratchet-20261014.yml` runs exclusively on **GitHub-hosted ubuntu-latest**, with `contents: read`, immutable checkout and `persist-credentials: false`. It includes a candidate-head check and full Git history for the comparison. It has no secret, VPS/runner, Actions write, force-start, worker/Firefox, SQLite or release permission.

## Known boundary

This conservative syntactic source gate is **not** a general YAML parser or proof of live runner state. It screens newly changed candidate workflows; it does not remediate legacy files, verify global GitHub concurrency-group uniqueness, prove queue safety, authorize a merge, or promote production. Before integrating, review file ownership, PR-triggered self-hosted workflows such as #638, privileged dashboard recovery #1135 and serialized #580/PWQ-41/#1089. Do not create a PR or restart jobs merely to obtain a green check while those live gates remain open.

## Reproduction evidence

- The regression suite now also covers cancelled previous-revision VPS jobs, exact-Git inventory cohorts, YAML quoted false values and malformed Git ancestry. CI is run on exact immutable heads; counts and run URLs must be verified against the final SHA, never copied from earlier heads.
- [Hosted exact-head cancellation ratchet #38615456281](https://github.com/Zennay/zCloud/actions/runs/38615456281): terminal success on SHA `90b3c81fa32d1c9a5f8f2177f3829e59297a0abe` (prior to this documentation-only commit).
- A full repository regression run must be checked against this document's final branch SHA; hosted source proof does not imply current VPS health.
