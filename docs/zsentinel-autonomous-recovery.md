# zSentinel — autonomous portfolio unblocker (M0)

zSentinel is a **separate portfolio project** registered as `zsentinel` but implemented initially as a low-footprint, read-only diagnostic inside zCloud. zCloud remains the canonical scheduler and state writer: this project does not create duplicate SQLite queues, worker allocations or recovery owners.

**Notion Project HQ:** https://app.notion.com/p/3fb9e19ac95581f2b686f01ed9e4dee0

## Canonical operating systems (not copies)

- Project Genesis OS: https://app.notion.com/p/3dd9e19ac9558199bd68cf1765a642bd
- Project Company OS: https://app.notion.com/p/3dd9e19ac9558167957ff454187edf30
- Portfolio Control Tower OS: https://app.notion.com/p/3dd9e19ac955815ebdf0ccbc021c5155
- zCloud HQ: https://app.notion.com/p/3e79e19ac955811d8fd4d35d176bdeb8

Always read current project HQ, Handoff, canonical issues, current commit and claim ownership. Notion is intent/decisions; GitHub is executable source and exact-head CI; runtime/queue receipts are execution truth. The different sources can conflict and must **not** be silently reconciled by guesses.

## First shipping slice: reproducible fail-closed diagnosis

`scripts/zsentinel_diagnose.py` consumes one **trusted, pre-collected, evidence-only JSON snapshot** (no network/credentials). It emits redacted per-project states with stable incident fingerprints. The script cannot execute shell commands, edit files, call Notion, dispatch workflows or restart any service. Non-read-only AI repair orchestration is a separate, later gate.

Example input (replace times with a **recent** observation, and use a real job head SHA if known):

```json
{
  "schema_version": 1,
  "observed_at": "2026-10-16T19:40:00+00:00",
  "projects": [
    {
      "id": "cloud",
      "notion": {"posture": "Active"},
      "github": {
        "job_status": "queued",
        "observed_at": "2026-10-16T19:40:00+00:00",
        "queued_at": "2026-10-16T18:45:00+00:00",
        "runner_assigned": false,
        "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    }
  ]
}
```

```sh
python3 scripts/zsentinel_diagnose.py /path/to/recent-evidence.json
python3 -m unittest tests.test_zsentinel_diagnose -v
python3 -m unittest tests.test_portfolio_onboarding_20261016 -v
```

The classifier intentionally cannot claim a project is *recovered*: successful CI is not live evidence. At most it detects a high-confidence symptom that deserves investigation. Require independent verification that the active runner/service/queue and user-visible product are actually healthy before updating resolution.

## Continuous AI reasoning workflow

On each authorized scheduled review:

1. Query current P0/open zCloud issues first; inspect current Notion work queue, claim/PR ownership, remaining time gates and resource budgets.
2. Prefer existing exact-head CI/job and durable queue/receipt evidence over screenshots or stale status messages. Never infer "down" from a project that is legitimately paused, undergoing a frozen holdout, waiting for a device/user gate or simply queued on a busy runner.
3. Choose the highest impact *safe and unowned* incident. Run a bounded read-only reproduction; no parallel edits of claimed code.
4. If a safe fix exists, change narrow code + regression tests in a fresh branch and open PR. Read existing security CI. Reproduce on the exact candidate head and record proof.
5. If incident belongs to someone else, attach concise reproducible evidence to **their existing issue**, not a second task, and pick another safely executable independent issue.
6. Never cancel competing jobs, bypass review, force deploy, restart production, change SQLite state or broaden authorization. Such fixes remain under the existing serial release/runner owners.
7. Confirm recovery on independent actual runtime evidence, not merely test green. Deduplicate repeat reports with fingerprint, cooldown and previous incident state.

## Cases to keep distinct

- Legitimate frozen trading holdout: **intentional_wait** until boundary; do not start provider I/O.
- Notion Human Action / unavailable phone: **intentional_wait**; work on other code instead.
- Existing owner PR or serialized release: **owner_held**; preserve branch ownership.
- zCloud runner jobs queued without assignment for 30+ minutes and fresh evidence: **stalled**, investigate #1326 via an independent channel rather than a self-hosted recovery job on the missing runner.
- Fresh failed exact-head CI: **stalled**, inspect actual error and ship isolated reproducible fix.
- Successful CI with no VPS acceptance: **unknown**, not "recovered".

## Security/release boundary

New projects MixLab, Fuckball, Tuddy and zSentinel appear in the required `vps-execution-policy.json` routing inventory, **but this is not runnable admission**: all four use `external_gate`, `auto_start=false`, with one AI slot max. Running jobs still requires independent owner, queue, resource and release authorization. The project's scoped ChatGPT automation may inspect GitHub/Notion and propose/commit bounded non-production patches, but **registry changes alone start no VPS worker**. Production release stays with #580/PWQ-41/#1089, runner recovery with #1326, writer locks with #1447 and zCloud source ownership with existing active workers. Do not create hidden unrestricted agents.

**Next integration milestone:** an authenticated *read-only* collector that polls Notion/GitHub and produces the input contract with durable observation IDs, then a tightly scoped PR-only repair executor. Collectors must redact credentials and untrusted prompt content.
