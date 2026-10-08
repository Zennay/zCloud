# Deploy evidence freshness snapshot (offline)

This is an **advisory, non-authorizing** check. It cannot prove the
GitHub API response, gate inventory, repository ownership, or production
state is authentic. An operator must freshly collect each input against
the *same* main revision. A green local result is never permission to
merge, run a workflow, or deploy.

Run:

```sh
python3 scripts/zcloud_deploy_evidence_freshness.py snapshot.json
python3 -m unittest tests.test_zcloud_deploy_evidence_freshness
```

The JSON snapshot must contain `main_sha`, `candidate_base_sha`,
`candidate_head_sha` (full lowercase 40-character commit IDs), an
integer `behind` equal to 0, `serialized_gate_released: true`, and
`checks` with `regression`, `cpu`, and `dashboard` objects. Each
required check must have `conclusion: "success"` and a `head_sha`
matching `candidate_head_sha`. Missing, incomplete, stale, or
non-success evidence fails closed.

Even on successful validation, output explicitly includes
`deploy_authorized: false` and `mutation_performed: false`. Before
any real admission, the independent serialized owner must re-check
#580/PWQ-41, #1089, current main, complete open-PR and PR-less branch
ownership, all applicable exact-head proofs, and the production release
contract. No code in this helper contacts an external service or performs
mutations.
