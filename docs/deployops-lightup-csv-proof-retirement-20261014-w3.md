# LightUp CSV export historical proof retirement — 2026-10-14

## Scope and provenance

This deploy-ops operation removes exactly four **obsolete GitHub Actions
entrypoints**, each running a pinned historical LightUp PR on the permanent
zCloud VPS. At retirement selection zCloud main was
`566f7663be12ea13c1cabdd43d6501dd9cad03a6`.

| LightUp PR | Lifecycle confirmed using GitHub PR API | Pinned proof head | Final merge commit |
| --- | --- | --- | --- |
| [#956](https://github.com/Zennay/LightUp/pull/956) | Closed, merged | `e782012b48eb87a201eeac3edf79b4e1d7f027cb` | `d841b254f29866ab7b678708cef8e8e89dc5381d` |
| [#959](https://github.com/Zennay/LightUp/pull/959) | Closed, merged | `f26cd5ff4e3ca941af62ccf6e539a6d084297898` | `21b25ecf0347cbe1aaae848da7d162e9810f9cc9` |
| [#960](https://github.com/Zennay/LightUp/pull/960) | Closed, merged | `5e86335eac4177bcc261fea28df38b18465daf98` | `33708e614401f7c8db44a6a1a2931c3802264fe4` |
| [#962](https://github.com/Zennay/LightUp/pull/962) | Closed, merged | `2e4898c9383cc622f9f5b0e1a87c5152bb7956cc` | `dd4072cdd1a2bc44a752ccbb7b1d0b6d56559da1` |

Their old `.github/workflows/lightup-pr<N>-exact-head-proof.yml` paths
were `workflow_dispatch` capable and auto-ran after modification on main.
They each requested `[self-hosted, zcloud, vps]`, targeted static PR heads
and did not certify current LightUp main. Their sole retained purpose was
archival re-execution. For historical provenance retain GitHub commit and
original Actions receipts rather than preserving a runnable obsolete workflow.

## Ownership and explicit exclusions

- Exact file path and active PR text searches returned no owner for these
  four paths; matching un-PR'd branch searches for each PR stem and for the
  combined CSV retirement task returned no active branch.
- The existing `lightup-export-obsolete-proof-cleanup.yml` references
  the historical **name** of the PR960 workflow when checking a single
  old Actions run. That **different** workflow and its cancellation authority
  are not changed or invoked by this deletion.
- [LightUp PR #963](https://github.com/Zennay/LightUp/pull/963)
  remained **open** at selection. Its separate
  `lightup-pr963-exact-head-proof.yml` is explicitly preserved.
- Other LightUp terminal proof owners, including zCloud PR #755 and the
  production activation/recovery lanes #580/PWQ-41 and #1089, are untouched.
- Removing YAML does not cancel existing in-progress/queued historical jobs;
  no Actions write permission, queue control, runner recovery, service restart,
  browser/worker/SQLite operation or live LightUp assessment was performed.

## Acceptance

- A new PR-only GitHub-hosted, read-only test asserts the four paths and
  pinned old source hashes are gone from active workflows, the still-open
  PR963 path remains, and no privileged runtime permission is introduced.
- Require focused hosted and full zCloud regression at the **exact PR head**
  before integration. A source-only squash merge uses `[skip ci]` to avoid
  invoking unrelated privileged `workflow_run` production deployment.
- Green source CI **does not prove** the P0 runner listener is online,
  healthy production status or successful real VPS deployment.
