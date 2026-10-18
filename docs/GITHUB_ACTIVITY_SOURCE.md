# zCloud GitHub activity observer (read-only source)

Tracks https://github.com/Zennay/zCloud/issues/1518. The local activity stream cannot represent a remote PR, merge or GitHub Actions run absent from the VPS checkout. This independent source provides bounded real GitHub observations **without modifying** the serialized server owner, production SQLite, eventstream, browser workers or active queues.

## Use

From this repository, on an approved operator host:

    python3 scripts/zcloud_github_activity_source.py --project cloud --hours 24 --max-events 80

For two or three scoped projects, request repeated --project options; use separate invocations for larger portfolios to stay within the 24-request budget. A private repository requires an existing GitHub token in the process environment with the smallest read-only permissions necessary for pulls and Actions. **Do not put a token in an argument, file, dashboard, issue or committed config.** There is no token persisted by either script. Without access the individual project reports repository_inaccessible / unavailable; an outage never becomes an empty, complete feed.

Readable operational output, with links and explicit no-deployment claims:

    python3 scripts/zcloud_github_activity_source.py --project cloud --project supa | python3 scripts/zcloud_github_activity_digest.py

Mobile-readable self-contained **offline HTML view** (no JS, remote assets, tracking, server or credential persistence):

    python3 scripts/zcloud_github_activity_source.py --project cloud --hours 24 --max-events 60 | python3 scripts/zcloud_github_activity_html.py > /tmp/zcloud-activity-snapshot.html

Open that local file in a browser. It displays expandable accessible per-project timelines for PRs, workflows and SHA-bound main commits, with explicit incomplete-source and no-deployment warnings. Generated HTML escapes every data field and derives links solely from validated canonical repository names/IDs.

The observer's output is zcloud-github-activity-readonly-v1 JSON containing event stable ID, canonical project/repository, event kind, source timestamp, separately recorded observation timestamp and coverage. It exposes no PR title, body, review content, workflow log, API response body, runner arguments or credentials. All repository identities are drawn from the canonical projects.json GitHub HTTPS allowlist; CLI parameters cannot name arbitrary repositories. The reader makes at most 30 HTTP GETs across up to three selected projects: up to five pages of ten verbose PRs, two pages of forty Actions runs, and two pages of twenty SHA-bound canonical-main commits per project. Source responses and the local registry have byte caps. The output explicitly marks when a source page cap truncates the older history; even a short --max-events result alternates PRs and CI so Actions volume cannot hide every PR.

Kinds: pr_created means created, not merged; pr_updated means changed, not accepted; pr_merged means merged_at exists but is **not** verified on canonical main (main_merged=false); ci_run provides GitHub Actions status, conclusion and SHA, not deployment or worker resumption; main_commit is the SHA of a commit returned from GitHub's canonical main-history endpoint (main_merged=true), not live deployment. No private commit messages, authors or titles are retained.

Coverage remains partial_sources even on successful GitHub reads because branch-only commits, all pagination, local live runtime and cross-provider deduplication are not fully synchronized. Failed and partial pages become explicit partial_error or unavailable states; never infer there was zero activity. The digest validates a strict receipt and renders a bounded, metadata-only human handoff. No HTTP server is started.

## Integration boundary

The central server and /api/activity are **not** modified here. Central eventstream, runtime/writer ownership, canonical main integration, release gating and worker scheduling stay with existing owners (#580/#1089, #776/#777 and #1519). A source-only PR or successful local syntax/build check is **not** a live feed repair. Before dashboard wiring, the integration owner must reconcile GitHub/local event identity, per-project fair paging/cursors, branch coverage, private-repo access, sync budgets and exact-main provenance. Do not replace the existing local events or mark the UI fully live from these partial source observations.

No QA/regression suite is introduced or manually executed under the 17 October product-delivery instruction. Existing mandatory GitHub-hosted CI, branch protection and production review remain intact.
