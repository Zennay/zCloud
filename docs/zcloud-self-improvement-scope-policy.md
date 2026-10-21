# zCloud self-improvement scope policy

zCloud may let an autonomous worker propose new improvement ideas, but that permission must not become a route for silently changing the project's mission or its safety rules.

The versioned `self-improvement-scope-v1` contract separates three proposal kinds:

- `backlog_idea` — a bounded improvement idea may be admitted to a backlog by a later runtime integration.
- `mission_change` — never auto-admitted; the policy returns `HUMAN_APPROVAL_REQUIRED`.
- `safety_change` — never auto-admitted; the policy returns `HUMAN_APPROVAL_REQUIRED`.

The proposal payload is intentionally narrow. It contains a schema version, machine proposal ID, kind, bounded summary and optional bounded machine-readable evidence references. Fields that could smuggle an executable mission/safety patch, approval flag, raw prompt or immediate-apply instruction are rejected rather than ignored.

The evaluator never echoes the proposal summary or evidence references into its decision output. This keeps CI/runtime evidence bounded while preserving enough information to correlate the decision.

This contract is a policy primitive, not yet the backlog writer. Runtime integration must call it before an autonomous zCloud self-improvement proposal is appended or applied; the parent roadmap item should remain open until that integration is live and proven.
