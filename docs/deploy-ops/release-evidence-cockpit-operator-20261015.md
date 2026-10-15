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

Open the resulting HTML locally. It includes bounded **Veilige volgende controles** (next safe observations) tied to missing production, runner, deploy or timestamp evidence; it never suggests an automatic restart or declares authorization. Both outputs remain local in manual mode; no upload, workflow dispatch, branch update or production mutation is performed by the script. The separately admitted hosted-only CI workflow intentionally uploads the sanitized report as a short-lived artifact.

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
   newer failure, and a missing status is not success.
3. The first 100 most recently returned main deploy runs from
   \`zcloud-vps-deploy.yml\` are inspected. The latest matching run
   for **the observed SHA** is identified by GitHub run ID and attempt.
   Up to 100 returned jobs must be fully accounted for; self-hosted
   \`zcloud,vps\` jobs are classified as canonical-assigned, unexpected-runner,
   queued-unassigned or unverified. Each matching run's GitHub `updated_at`
   is validated; observations over 15 minutes old receive a stale-evidence
   warning, future-dated or malformed timestamps are rejected. Even fresh
   historical assignment is NOT a live listener healthcheck. A queued job with
   runner ID 0 is NOT proof that the runner listener is online or offline.
4. Canonical \`main\` is re-read at the end; SHA drift refuses the
   evidence snapshot. All malformed and over-limit inputs refuse proof.

**Limits:** The workflow-run view is the *first page* of 100 and
therefore a bounded, not all-time exhaustive, history. The report cannot
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
signals, truncated jobs, bad provenance, bool masquerading as integer,
duplicate JSON keys, NaN and XSS/HTML escaping. Production writer files
and other workers' PR-owned paths are deliberately untouched.

**Ownership:** isolated add-only files on
\`deployops/release-evidence-cockpit-20261015-w1\`. Review as a standalone
draft; never merge or deploy without independent review and release approval.
