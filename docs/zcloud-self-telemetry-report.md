# zCloud self-telemetry report

The zCloud roadmap requires project-specific telemetry without baking project logic into the generic dashboard. The adapter-coverage audit currently classifies the `cloud` project as `generic_only`.

This add-only reporter creates an evidence-backed foundation for zCloud's own control-plane telemetry while deliberately avoiding the serialized runtime/writer window.

- Scorecard progress comes only from the canonical `cloud` row in `projects.json`.
- GitHub evidence resolves the exact `Zennay/zCloud` main SHA and paginates open pull requests through a hard five-page bound.
- The total open-PR count is exact inside that bound; only the newest 64 PR numbers are emitted and a truncation flag makes the projection limit explicit.
- CI is tied to that exact main SHA. Exact-head GitHub Actions runs are preferred; commit-status contexts are the fallback.
- Missing CI remains `not_configured`; it is never turned into an invented green state.
- PR titles, bodies, workflow logs, Notion content, tokens, prompts and runtime command payloads are not projected.

## Integration boundary

`scripts/zcloud_self_telemetry_report.py` is read-only. It does not modify `enhancements.py`, `server.py`, the dashboard, SQLite, queue state, browser automation, services or deployment state.

A later adapter-integration change may consume this report only after the current serialized control-plane/runtime owner is free and exact-head regression remains green. Until then, this PR is a reporting foundation and must not mark the project-specific adapter roadmap item complete.
