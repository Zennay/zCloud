# Offline deploy-gate snapshot review contract

This document covers `scripts/deploy_gate_snapshot_offline_20261008.py`.
It is an **advisory check only**, never a release permit.

The caller must independently obtain a fresh, trusted, complete snapshot of
the exact current `main` and serialized owner inventory. The script does not
fetch remote state. Snapshot format:

```json
{
  "repository": "Zennay/zCloud",
  "main_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "captured_at": "2026-10-08T00:59:00Z",
  "gates": [
    {"number": 580, "state": "closed", "release_confirmed": true},
    {"number": 1089, "state": "merged", "release_confirmed": true}
  ]
}
```

Numbers and sample SHA above are **illustrative**, not live evidence.
Run with `python3 scripts/deploy_gate_snapshot_offline_20261008.py snapshot.json`.
Exit code 0 means only that the locally supplied structure passes the
offline checks; exit 1 means rejected; exit 2 means unreadable/invalid JSON.

Important limitations:
- A timestamp inside user-supplied JSON is not a trustworthy freshness attestation.
- The script cannot verify live GitHub state, main drift, other active
  serialized writers, workflow results, changed-file ownership, branch overlap,
  or post-deploy health.
- Never treat `eligible_for_review=true` as permission to merge, deploy,
  mutate SQLite, cancel runners, or change browser/service state.
- For admission, use the canonical live exact-head gate inventory, complete
  open-PR and PR-less-branch ownership checks, required CI, and guarded
  production receipt. #580/PWQ-41 and #1089 remain independently controlled.
- Never import real credentials, logs, prompts, or sensitive deployment data
  into captured snapshots; use identifiers and bounded evidence only.

Test module:
`python3 -m unittest tests/test_deploy_gate_snapshot_offline_20261008.py`
(runner execution must be verified separately).
