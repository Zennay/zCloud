# zCloud worker continuity post-hotfix verification (2026-10-11)

This file is a non-deploying exact-main CI carrier. It exists to trigger the
GitHub-hosted `zCloud regression smoke` workflow on the reviewed `worker/**`
event path without opening a PR or executing unreviewed code on the VPS.

Verified merged code revisions:
- `9a1af583`: manual-only bulk force push, no forced 7, preserve pending command ownership.
- `4b392b8b`: manual-only global Firefox restart; queue watchdog is runtime/runner-only and gates main provenance.
- `14ae1aae`: retire expired overnight 8-worker five-minute timer.

Hosted focused runs: 38090879483, 38091071622, 38091157708 (all success).
Reviewed zSSH read-only server probe 38091234185 verified deployed code revision
`a57439e`, not current source HEAD; no deployment/DB/queue mutation is
authorized by this smoke document.

Required live completion remains independent: after governed deploy/source
reconciliation, prove per-worker prompt-sent, generation-progress and
queue-result evidence plus Firefox/memory stability. Do not conflate an
allocated slot, a heartbeat, or a queued command with useful AI output.
