# Worker Completion Controller

Added in response to the 2026-10-09 worker audit: zCloud's workers were
reliably *starting* new roadmap work but the portfolio queue had no concept
of a project's already-open, unfinished GitHub PR work, so a worker finishing
a cycle was always handed a brand-new "roadmap work package" instead of being
pointed at the PR it (or another worker) already had open. That produced the
~1,250-open-PR backlog the audit measured.

## What it does

1. **Classifies** every open pull request for a project into one label:
   `mergeable`, `conflicting`, `ci_failing`, `needs_review`,
   `draft_in_progress`, `stale`, or `possible_duplicate`
   (`completion_controller.classify_pr`, pure/unit-tested, no network).
2. **Prefers finishing over starting.** `portfolio_queue_audit`'s refill step
   now calls `portfolio_completion_first_continuation(project_id)` before
   `portfolio_write_continuation(project_id)`. If the project has any
   unfinished work on record, the worker is handed a task that names the
   specific PR and tells it not to open a new one; a brand-new roadmap
   package is only ever generated when there is none.
3. **Tracks evidence-backed completion metrics** per project: commits, CI
   terminal success/failure/cancellation, merges, deploys, tasks
   started/completed, and the timestamp of last *material* progress
   (commit/CI-success/merge/deploy — not just a chat opening).
   `GET /api/completion` returns the full dashboard payload, including the
   portfolio-wide **Material Completion Rate** (`tasks_completed /
   tasks_started`) and a stagnation report.
4. **Auto-merges conservatively**, only PRs that are simultaneously:
   not draft, `mergeable_state == "clean"`, every check run completed with a
   green conclusion, no `CHANGES_REQUESTED` review, and no "do not merge yet"
   phrase in the title/body (a plain-prose veto, since some PRs — e.g. #580 —
   only express this in free text, not a label or draft flag). Classification
   happens once (on sync); the merge step **re-fetches and re-checks the PR
   immediately before merging**, so a PR that regressed between classification
   and merge is skipped, not merged on stale evidence. GitHub's own branch
   protection is the final backstop: a merge attempt that doesn't actually
   satisfy required reviews/checks is rejected server-side regardless of what
   this controller believes.

## Where the pieces live

| Piece | File |
|---|---|
| Classification + SQLite schema + metrics | `completion_controller.py` |
| Queue-refill integration | `server.py`: `portfolio_completion_first_continuation`, `portfolio_queue_audit` |
| Dashboard API | `server.py`: `GET/POST /api/completion` |
| GitHub → dashboard sync + auto-merge | `scripts/zcloud_completion_sync.py` |
| Scheduled job (zCloud's own PRs) | `.github/workflows/zcloud-completion-controller-sync.yml` |
| Tests | `tests/test_completion_controller.py`, `tests/test_completion_controller_workflow.py`, `tests/test_zcloud_completion_sync.py` |

## `POST /api/completion` actions

- `sync-pr-state`: `{project_id, repo, pulls: [{pr, checks}, ...], authoritative}` —
  classifies and upserts each PR's state; with `authoritative: true` also
  drops rows for PRs no longer in the `pulls` list (merged/closed elsewhere).
- `progress-event`: `{project_id, kind, detail?, source_url?, amount?}` where
  `kind` is one of `commit`, `ci_success`, `ci_failure`, `ci_cancelled`,
  `merge`, `deploy`.
- `task-completed`: `{project_id}` — increments the Material Completion Rate
  denominator's counterpart.

All three go through the existing `action_request_allowed` gate (localhost or
a trusted admin origin), the same as `/api/portfolio-queue`.

## Extending coverage to the other 7 portfolio repos

Today only zCloud's own repo is wired up, because the default
`GITHUB_TOKEN` a workflow run receives is scoped to the repo it runs in —
there is no existing cross-repo token in this portfolio (verified: every
workflow in this repo uses only `secrets.GITHUB_TOKEN`), and several
`.github/workflows/*.yml` files already present in this repo under
other projects' names (e.g. `lightup-*`, `raiseai-*`, `ftmo-*`) turned out to
be exactly the kind of copy-paste-across-repos mistake the audit flagged for
HaxLab's FTMO-named workflow runs — not a working cross-repo mechanism to
build on.

To extend: add the same two steps (`zcloud_completion_sync.py` +
`--project-id <id>`) to each of LightUp, Supa, RaiseAI, HaxLab, zGuard and
FTMO's **own** `.github/workflows/`, each using that repo's own
`github.token`, each posting to `http://127.0.0.1:8765/api/completion` —
which only works if that repo also runs a self-hosted runner on the same VPS
(`vps-bb300bba`). Confirm that before copying the workflow; if a repo's
workers run elsewhere, the sync step needs a reachable dashboard URL instead
of localhost, which is a deliberate decision to make per-repo rather than
assume.

## Guardrails preserved

- No branch-protection, review, or production-safety rule is bypassed —
  merges still go through GitHub's normal merge API and protected-branch
  checks.
- Nothing is auto-closed. `possible_duplicate`-labelled PRs are surfaced for
  a human/worker decision, never closed automatically — wrongly closing real
  work is worse than leaving a duplicate open one more cycle.
- `stale` PRs are flagged, not touched; a worker assigned to one decides
  whether to revive or close it with a reason.
