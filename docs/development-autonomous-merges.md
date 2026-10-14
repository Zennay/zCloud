# Development-stage autonomous integration policy

Applies to every zCloud project worker while its product is in active development. The project's current phase in Notion determines development vs. rollout. The central browser-dispatched instructions in `server.py::project_runner_prompt` implement this policy.

## Development (default until rollout)

1. Prioritize actionable open issues and finish existing owned PRs before spawning replacements; check current base, active lane/worker ownership and any overlapping branch.
2. For a PR within the worker's scope: resolve conflicts, make drafts review-ready, run the applicable tests against the **exact current PR head**, and inspect GitHub-required checks and branch protection. An absent optional human approval is **not** a reason to stop. Merge the PR into the actual target branch yourself when every GitHub-enforced condition is satisfied. Do not force-push protected branches or bypass required reviews/checks.
3. After merging, confirm GitHub reports merged and record the exact main commit. Move immediately to the next unclaimed high-impact issue or product task; do not claim that a source merge is a live deployment.
4. If CI fails, fix it. If CI has not completed, continue safe, non-overlapping work. If required approvals, branch protection, ownership, or a specific safety gate block one PR, leave only that PR gated and work on another safe task.
5. Do not block development merges just because **separate** VPS deploy, production database, paid cloud, active security testing, third-party data rights, or FTMO live trading/holdout approvals have not been granted. Conversely, a merged PR never grants those permissions.

## After production rollout

Switch to the project's release policy: deployment approvals, stricter testing, environment protection, data migration, independent review and production change windows as appropriate. A development-only merge instruction never waives these. Record source merged vs. deployed vs. live verified as distinct outcomes.

## Worker acceptance

- Applicable exact-head checks green, no conflicted ownership, GitHub protections satisfied, merge SHA confirmed.
- PR-first and no endless draft/test-only loops.
- Other safe tasks continue when a single PR is gated.
- A blocked or failed release gate does not become automatic rollout permission.
