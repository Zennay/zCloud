# uLab control-plane telemetry report

The uLab roadmap asks zCloud to expose milestone/epic progress, CI status, the
active implementation gate and open pull-request state without hard-coding
uLab behavior into the generic dashboard.

This add-only reporter deliberately keeps the evidence sources separate:

- **Milestone progress** comes from zCloud's canonical `projects.json`
  scorecard fields, which are already the source used by the uLab telemetry
  adapter.
- **Active implementation gates** come from the same explicit `human_gates`
  records. A gate is not inferred from issue age or prose.
- **GitHub main revision and open PRs** are read directly from `Zennay/uLab`.
- **CI status** is tied to the exact resolved main SHA. Exact-head GitHub
  Actions runs are preferred; commit-status contexts are the fallback. If
  neither exists, the state is `not_configured`, never an invented success.

The output is bounded and intentionally does not emit workflow logs, PR titles,
issue bodies, GitHub tokens, Notion content or command output.

## Integration boundary

`scripts/zcloud_ulab_telemetry_report.py` is a read-only reporting
foundation. It does not modify `enhancements.py`, `server.py`, the
dashboard, uLab, SQLite or production state. A later integration may consume
this report through the generic telemetry adapter only after the current
`enhancements.py` owner is free and exact-head regression remains green.
