# zCloud migration records

Any change-set that introduces or changes a durable database/config migration must carry a migration record in this directory. The CI guard treats SQL DDL, canonical config schema-version changes, the config-version registry and migration-named implementation paths as migration-risk surfaces.

Use a dated, descriptive filename such as `20261006-runner-correlation.md`. A record must contain substantive `## Scope`, `## Forward`, `## Rollback` and `## Validation` sections. The rollback section must describe a real recovery path; placeholders such as “N/A” or “automatic” are rejected.

This contract is intentionally fail-closed but bounded: ordinary application changes, tests, documentation and CI files do not become migration-risk merely because they mention SQL examples.
