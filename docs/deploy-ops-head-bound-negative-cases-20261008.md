# Deploy-ops acceptance: negative-case review matrix

Companion to [the head-bound ledger](deploy-ops-head-bound-admission-ledger-20261008.md). These are **manual negative acceptance cases** for the release reviewer, not claims that CI has executed them. All expected decisions are **DEFER**; no case grants a write.

| Case | Input condition | Required outcome |
| --- | --- | --- |
| N01 | Check is green but `run.head_sha != candidate.head_sha` | DEFER: head-bound proof missing |
| N02 | Check is queued or in progress | DEFER: conclusion is not success |
| N03 | Required check skipped, cancelled or timed out | DEFER: required evidence missing |
| N04 | Main advances after compare or ownership snapshot | DEFER: refresh both snapshots |
| N05 | Candidate is behind main by at least one commit | DEFER: owner restack and retest |
| N06 | Candidate head moves after validation | DEFER: all checks must bind new head |
| N07 | Open PR overlaps any candidate changed path | DEFER: coordinate distinct owner |
| N08 | PR-less active branch overlaps changed path | DEFER: don't take over branch |
| N09 | #580 is open even if #1089 is closed | DEFER: serialized gate not released |
| N10 | #1089 is open even if #580 is closed | DEFER: serialized gate not released |
| N11 | Both gates closed, but another live writer discovered | DEFER: resolve additional writer |
| N12 | Successful regression but missing protected production receipt | DEFER: deploy not authorized |
| N13 | Report has no explicit owner or missing changed-path inventory | DEFER: incomplete ownership evidence |
| N14 | Old predecessor #576 closed but successor #1089 still open | DEFER: do not use historical gate |

## Reviewer protocol

1. Capture one timestamped tuple and classify every applicable case.
2. Independently check live GitHub facts for PR state, head, main, check runs and overlaps.
3. Record which exact run IDs and SHAs justify every positive assertion.
4. On any negative case, leave `review_ready=false`, `merge_authorized=false`, `deploy_authorized=false` unless a *separate* designated admission authority provides fresh explicit approval.
5. After restack or changed gate state, **restart** the review; never patch an old tuple without a new snapshot.

This matrix intentionally avoids commands that could deploy, merge, change GitHub rulesets, start/stop services, cancel runners, or write production state.
