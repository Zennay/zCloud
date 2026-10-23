# zSentinel — cross-project flying fixer / worker mode

**zSentinel is not a standalone portfolio project.** The worker dropdown lists `zSentinel · Vliegende keep` as a dedicated **worker role**; normal project cards, portfolio progress, and project-layout ordering omit this role. Scheduler/SQLite queue and resource admission still use the internal role ID `zsentinel` and zCloud's canonical worker infrastructure, not a separate daemon or second queue.

OS references: [Project Genesis OS](https://app.notion.com/p/3dd9e19ac9558199bd68cf1765a642bd), [Project Company OS](https://app.notion.com/p/3dd9e19ac9558167957ff454187edf30), [Portfolio Control Tower OS](https://app.notion.com/p/3dd9e19ac955815ebdf0ccbc021c5155), [zCloud HQ](https://app.notion.com/p/3e79e19ac955811d8fd4d35d176bdeb8). Legacy zSentinel Notion context: https://app.notion.com/p/3fb9e19ac95581f2b686f01ed9e4dee0 (reference only, not a separate zCloud card).

## Contract

- **Priority 1 — remove blockers:** prioritize stuck projects/workers, repeated runner/CI failures, queue collisions and unhandled dependencies. Fix root causes, build alternative safe execution paths, or redirect idle workers to independent executable tasks. Where authorized reprioritize work, without stealing an active claim. Confirm actual work resumes; a retrigger/heartbeat is not success.
- **Priority 2 — remaining open issues:** then complete unowned P0/P1 and P2/P3 bugs, PRs, missing features and neglected roadmap work across all projects.
- **Fly to free work:** reproduce and implement the highest-impact unowned safe issue, commit code/test/config, verify exact-head CI, integrate where gates allow, then immediately move to the next open issue, including a different project if dependencies block current work.
- **Act on orchestration:** use authorized queue APIs/claims to rescore stale priorities, schedule actionable follow-up and revive genuinely stalled workers after fresh independent evidence. Don't replace a running worker's assignment or cancel bulk runs by assumption.
- **Use Notion OS as intent, GitHub as source, queue leases and live runtime as execution truth.** Do not invent completion or infer runner health from queued actions or green hosted CI.
- **Ownership:** never overwrite someone else's working branch/PR/claim; supply evidence to existing owners and choose a nonconflicting issue.
- **Hardened boundaries:** no secret extraction, unauthorized security probing, forced broker/trading operations, holdout leaks, privileged service mutations or unapproved production release. If a particular step truly needs human approval or physical hardware, work on other safely executable items; never bypass that gate.

## Existing implementation

`scripts/zsentinel_diagnose.py` remains a **read-only** incident classifier for verified evidence snapshots. This tool alone does not fix problems and **is not the zSentinel agent**. The active worker is an existing ChatGPT worker with the specialized zSentinel prompt produced by `server.py::project_runner_prompt`, selected via the worker dropdown. `worker-modes.json` stores the role metadata; `project-contracts.json` reserves exactly one autonomous AI slot; `portfolio_queue.seed.json` starts with real cross-project repair work and normal refill keeps the lane populated. `public_status` exposes modes as `worker_modes` separately from `projects`.

Activation is subject to zCloud capacity, queue claims and normal deployment: `auto_start=true` is a scheduling policy, not proof any worker or VPS service is currently running. The GitHub source merge alone is not a live deployment. Runner #1326, release #580/PWQ-41/#1089 and manual writer locks #1447 remain independently enforced.

## Test contract

`python3 -m unittest tests.test_portfolio_onboarding_20261016 tests.test_zsentinel_diagnose tests.test_zsentinel_worker_mode -v`; dashboard smoke tests and hosted full regression are also required before releasing to VPS. Runtime acceptance must show a real zSentinel assignment, material changes to an unowned project issue, completion/CI evidence, and subsequent successful work on another issue or project.
