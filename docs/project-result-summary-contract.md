# Project result summary contract

zCloud already has project-specific telemetry adapters for evidence-rich projects. This slice
adds a read-only projection for the dashboard intent “latest / current / best validated result”
without changing the live dashboard, scheduler, queue, browser or SQLite state.

## Contract

- `latest` means the newest adapter-backed result, even when it is not validated yet.
- `current` means the result currently promoted or otherwise active according to that adapter.
- `best` means only an adapter result that the adapter itself marks as the best validated result.
- Missing evidence stays missing. The projector never derives a timestamp, validation state or
  “best” result from file age, branch age or an unrelated metric.
- Projects without a project adapter return `source_mode=generic_only` and no fabricated result.
- Output is intentionally compact and user-facing. Raw source paths, arbitrary nested payloads,
  prompt contents and project evidence files are not returned.
- The projector is read-only. It has no database connection and no write/apply/deploy route.

This is an isolated control-plane read model. Dashboard/API integration remains a separate
serialized step because the current zCloud runtime writer window is owned elsewhere.
