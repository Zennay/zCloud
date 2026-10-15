# zCloud deploy-ops · release evidence cockpit (read-only, 15 October 2026)

**Scope:** An operator-friendly **advisory** view of three independent GitHub signals
for the *same* observed canonical \`main\` SHA. This standalone feature is not
a release gate and never writes GitHub, VPS, SQLite, queue, browser or services.

## Why this exists

- [#1326](https://github.com/Zennay/zCloud/issues/1326): a completed hosted CI
  check is not proof that the permanent VPS runner is currently accepting work.
- [#1447](https://github.com/Zennay/zCloud/issues/1447): production writer lock
  coverage must be resolved by the workflow owners; this cockpit makes **no**
  judgment about writer lock correctness.
- [#1204](https://github.com/Zennay/zCloud/issues/1204): no merge/admission
  from standalone source evidence; PR and PR-less branch ownership remain separate.

## Usage

Requires Python 3.10+; standard library only. Run it outside the repository
or in a trusted checkout. It makes **GET requests only** to the canonical
\`api.github.com/repos/Zennay/zCloud\` and does not execute GitHub responses:

\`\`\`bash
GITHUB_TOKEN="$GITHUB_READONLY_TOKEN" \
python3 scripts/zcloud_deployops_release_evidence_cockpit_20261015.py \
  --json /tmp/zcloud-release-evidence.json \
  --html /tmp/zcloud-release-evidence.html
\`\`\`

For a public repository, \`GITHUB_TOKEN\` can be omitted (rate limits may be
more restrictive). The token, if used, needs only read access to repository
metadata, commit statuses, Actions runs and jobs. Store token externally;
**never** pass it as a CLI argument or embed it in a generated artifact.

Open the resulting HTML locally. It includes bounded **Veilige volgende controles** (next safe observations) tied to missing production, runner, deploy or timestamp evidence; it never suggests an automatic restart or declares authorization. Human-readable Dutch warning labels are shown in HTML; the JSON warnings remain stable machine-readable codes. The offline report includes **canonical GitHub deep links** to the exact validated main commit and selected run, never URLs assembled from untrusted job names or inputs. Invalid/missing IDs cause those links to be omitted. Both outputs remain local in manual mode; no upload, workflow dispatch, branch update or production mutation is performed by the script. The separately admitted hosted-only CI workflow intentionally uploads the sanitized report as a short-lived artifact. If the API snapshot is malformed, incomplete or changes between reads (CLI exit code **2**), the hosted workflow now **uploads the sanitized diagnostic artifacts first and then marks CI red**. An unexpected crash or missing artifact still fails immediately; neither case can grant a release.

Exit codes: \`0\` means the requested GitHub snapshot was read without
local warnings (NOT approved); \`1\` means the snapshot was valid but at least
one production, workflow or runner signal is missing/non-success; \`2\`
means evidence was incomplete, unstable, malformed or unreachable. All three
outcomes retain \`release_authorized=false\`,
\`deployment_authorized=false\` and \`mutations_performed=false\`.

## What exactly is checked

1. GET \`git/ref/heads/main\` is required to be a 40-hex commit object.
2. The \`zcloud/vps-production\` commit statuses are queried for **that exact
   SHA**, with bounded full page traversal (max 5 × 100 statuses);
   newest matching timestamp/ID wins. A historic success cannot override a
   newer failure, and a missing status is not success. IDs must be unique,
   timestamps real and not future-dated. A success older than 15 minutes is
   *historical* and emits a freshness warning; it is never runtime health.
   After reading deploy/jobs, a **second exact-SHA production-status GET**
   checks the latest status ID, timestamp, state and matching event count.
   The second read checks a SHA-256 fingerprint of **all matching
   status-event tuples** as well as the latest ID/time/state and count. Even
   an older event changing without changing the latest status now refuses
   evidence. Two identical GETs still do not establish transactionally
   atomic GitHub state.
3. Main deploy runs from \`zcloud-vps-deploy.yml\` are inventoried across
   **all returned pages up to five pages of 100 runs**. Duplicate run IDs,
   malformed pages and missing/failed later pages **refuse the snapshot**.
   A repository with more than 500 runs instead yields an explicitly
   **incomplete 500-run advisory window**, always with a blocking warning
   and no release authority, never a claim that the entire history was checked. Where GitHub
   provides `total_count`, it must be a nonnegative strict integer, occur
   consistently on all pages and remain unchanged. On a complete inventory
   it must exactly match the final number of runs. On a capped window it
   must not be smaller than the scanned count; no completeness claim is made.
   The report marks whether this independent count was verified. The first page
   and **every other consumed page** are read a second time after job
   inspection; any changed page rejects the snapshot. Offset page reads are not transactionally atomic,
   and a completed bounded inventory does not replace release permission.
   The latest matching run for **the observed SHA** is identified by
   GitHub run ID and attempt.
   Up to 100 returned jobs must be fully accounted for; self-hosted
   \`zcloud,vps\` jobs are classified as canonical-assigned, unexpected-runner,
   queued-unassigned or unverified. Each GitHub job ID must be unique, and every
   job must belong to the selected *exact run, attempt and SHA*. A
   `completed` workflow without a conclusion, or a queued/in-progress workflow
   with an old terminal conclusion, is rejected as contradictory. A job that
   was assigned yet failed is reported separately from the runner assignment;
   an in-progress job never counts as completed success. After inspecting
   jobs the original run is fetched again, and any changed run, attempt,
   conclusion, SHA, event or updated timestamp refuses the entire snapshot.
   The selected job-list endpoint is also **read twice** and must match
   exactly: queued/assigned/completed transitions and newly added jobs
   cannot silently combine two different instants into one report.
   Each matching run's GitHub `updated_at`
   is validated; observations over 15 minutes old receive a stale-evidence
   warning, future-dated or malformed timestamps are rejected. Even fresh
   historical assignment is NOT a live listener healthcheck. A queued job with
   runner ID 0 is NOT proof that the runner listener is online or offline.
4. Canonical \`main\` is re-read at the end; SHA drift refuses the
   evidence snapshot. All malformed and over-limit inputs refuse proof.

**Limits:** The workflow-run view is a **bounded** page window (up to
500 entries). A high-volume repository can exceed this limit: the CLI then
returns a valid but **explicitly incomplete** observation, exit code 1,
with `deploy_run_inventory_incomplete` and all release flags false, not an
empty-history or all-green assertion. Two matching first pages do not prove
that unseen changes did not happen (ABA/offset race). Full-page re-reads
still do not constitute a database snapshot or production permission. The report cannot
confirm present VPS listener uptime, safe-idle, SQLite write access,
browser activity, callback provenance, protection rules or effective deploy
serialization. A green commit status or assigned historical job does not
authorize restart, re-arm, merge or release. It remains necessary to obtain
independent exact-HEAD CI, full PR/PR-less ownership checks, effective main
protection, owner review and the serialized production gate of
[#580](https://github.com/Zennay/zCloud/issues/580) /
PWQ-41 + [#1089](https://github.com/Zennay/zCloud/issues/1089).

## Test and handoff

\`\`\`bash
python3 -m unittest -v \
  tests.test_zcloud_deployops_release_evidence_cockpit_20261015
\`\`\`

Includes at least: main movement, stale production success, missing status,
queue without runner, unexpected runner, multiple contradictory route
signals, truncated jobs, multi-page run overlap and timeout/drift,
mismatched job run/attempt/SHA, failed vs assigned,
post-read reruns/conclusion drift, stale/future/duplicate production status,
bad provenance, bool masquerading as integer,
duplicate JSON keys, NaN, UTF-16 surrogate escapes, deeply nested JSON,
and XSS/HTML escaping. JSON parsing uses bounded bytes and iterative Unicode
validation; unexpected errors are reported only with static safe reason codes. Production writer files
and other workers' PR-owned paths are deliberately untouched.

**Ownership:** isolated add-only files on
\`deployops/release-evidence-cockpit-20261015-w1\`. Review as a standalone
draft; never merge or deploy without independent review and release approval.
