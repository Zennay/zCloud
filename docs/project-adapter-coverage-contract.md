# Project adapter coverage contract

This read-only report makes the remaining project-result coverage explicit without changing
the dashboard, runtime scheduler, queue or telemetry sources.

## Rules

- Only registry rows with `status=active` count toward coverage.
- An active project is `project_adapter` only when it is registered by the existing
  `enhancements.telemetry_adapter_projects()` contract.
- Every other active project remains `generic_only`; no adapter or result quality is inferred.
- Duplicate or malformed active project IDs fail closed.
- `projects.json` must be a bounded regular non-symlink JSON file.
- Output contains only project id/name and coverage mode. Repository paths, Notion URLs, source
  files, prompts and telemetry payloads are intentionally excluded.
- `--require-complete` is a validation-only gate: exit 3 means generic fallback still exists.

The report is evidence for deciding which project adapter should be implemented next. It is not
permission to fabricate project-specific metrics for projects whose durable source contract is
not yet defined.
