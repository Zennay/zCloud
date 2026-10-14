# zCloud deploy-ops: VPS cancellation-source ratchet (14 October 2026)

**Source-only.** This is a bounded extension of [#633](https://github.com/Zennay/zCloud/issues/633) / [#1193](https://github.com/Zennay/zCloud/issues/1193), not a replacement for ongoing workflow owners or production admission.

## Failure prevented

GitHub Actions `concurrency.cancel-in-progress` can terminate an already-running job sharing the same concurrency group. A privileged or merely read-only self-hosted diagnostic may disrupt ongoing work if a second run starts with a matching group. A manual workflow trigger alone is not sufficient protection.

The new `scripts/zcloud_deployops_cancellation_ratchet_20261014.py` compares two **exact 40-hex Git revisions**, scans only changed workflow blobs, and denies a changed workflow that combines a possible self-hosted or dynamically selected runner with effective/ambiguous global or job-level cancellation. Reusable-workflow delegation is treated as possible self-hosted execution. Unknown layouts, symlinks, oversized/undecodable files, and ambiguous Git source are denied.

Existing untouched workflows are not changed or declared secure; edited legacy cancellation debt requires owner review. Removing cancellation risk is allowed.

## Verification

```bash
python3 -m unittest -v tests.test_zcloud_deployops_cancellation_ratchet_20261014
python3 scripts/zcloud_deployops_cancellation_ratchet_20261014.py --base <exact-base-sha> --head <exact-head-sha>
```

The companion `.github/workflows/zcloud-deployops-cancellation-ratchet-20261014.yml` runs exclusively on **GitHub-hosted ubuntu-latest**, with `contents: read`, immutable checkout and `persist-credentials: false`. It includes a candidate-head check and full Git history for the comparison. It has no secret, VPS/runner, Actions write, force-start, worker/Firefox, SQLite or release permission.

## Known boundary

This conservative syntactic source gate is **not** a general YAML parser or proof of live runner state. It screens newly changed candidate workflows; it does not remediate legacy files, verify global GitHub concurrency-group uniqueness, prove queue safety, authorize a merge, or promote production. Before integrating, review file ownership, PR-triggered self-hosted workflows such as #638, privileged dashboard recovery #1135 and serialized #580/PWQ-41/#1089. Do not create a PR or restart jobs merely to obtain a green check while those live gates remain open.

## Reproduction evidence

- The first 20 local deterministic tests passed; another four targeted regressions cover trailing/global, inline/job cancellation and harmless comments.
- [Hosted exact-head cancellation ratchet #38615456281](https://github.com/Zennay/zCloud/actions/runs/38615456281): terminal success on SHA `90b3c81fa32d1c9a5f8f2177f3829e59297a0abe` (prior to this documentation-only commit).
- A full repository regression run must be checked against this document's final branch SHA; hosted source proof does not imply current VPS health.
