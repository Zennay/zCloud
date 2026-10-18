# zSentinel · product issue inbox

Purpose: immediately surface **existing**, materially impactful GitHub P0/P1 issues across canonical zCloud project repositories without taking active claims or reviving forbidden QA work.

Run from the repository root:

    python3 scripts/zsentinel_product_issue_inbox.py --project cloud --project supa > /tmp/zsentinel-inbox.html

Open the generated HTML locally; it is fully static, responsive, keyboard-accessible, and has no third-party assets, scripts or analytics. To see the entire configured portfolio (up to 18 projects, 54 bounded GitHub calls), use:

    python3 scripts/zsentinel_product_issue_inbox.py --all --max-items 100 > /tmp/zsentinel-inbox.html

For structured use (e.g. a read-only operator report):

    python3 scripts/zsentinel_product_issue_inbox.py --project cloud --format json

GitHub source identity comes **only** from the existing canonical projects.json allowlist with strict GitHub HTTPS origin, no redirects to arbitrary hosts. If the repository is private, provide a read-only `GITHUB_TOKEN` in the process environment. Never pass it in a URL, shell argument or output file. The program does not persist credentials, issue bodies, email addresses, GitHub usernames, workflow runner args or raw response headers.

## Explicit product-priority behavior

- Classifies a P0/P1 only from its **existing** `P0/P1` label, `priority:p0/p1` label, or explicit prefix in the GitHub issue title. No invented urgency based on subject matter.
- Filters out QA-only issues or quality-validation/regression-only labels, in accordance with the 17 October 2026 user instruction; shows the excluded count, but does not open or mutate them.
- Samples the oldest issues and the most recently updated issues per project, deduplicates canonical `repo#number` identities, then fairly interleaves project entries within priority class. This prevents a busy project from monopolizing the first screen.
- Shows issue age, GitHub assignee presence (as a Boolean only), links and project. An additional bounded recent-open-PR lookup surfaces **explicit issue number mentions**, distinguishes closing-keyword references from ordinary mentions, and never represents either as a live owner or proven fix. PR bodies are inspected in memory only, never exported. **An assignee is not a queue lease, an issue title is not proof of a reproducible defect, and missing issues in a bounded sample are not absence of issues.**
- Exposes every source's oldest/recent window status, partial/unavailable/rate-limit response and truncation. A partial provider query never automatically authorizes repair.
- Defaults to no GitHub writes, no issue/comment mutations, no SQLite access, no local browser worker or VPS service interaction, no CI/retry/merge/deploy action.

## Ownership and integration

This is a standalone **read-only operator product**, independent of the already claimed central server/eventstream/dashboards and of zCloud PR #1538 (GitHub PR/main-commit activity). It can help choose a candidate *only after* a fresh canonical Notion queue, SQLite `task_claims` / lease and GitHub owner PR check. The handoff/claim/merge/deployment authority stays entirely outside this tool.

This feature does not merge anything and does not mark Notion `Eligible=YES`. Existing reviewer, mandatory CI, security, production and rights gates still apply. No QA suite was written/run for this feature under the 17 October instruction.
