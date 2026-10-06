# Notion handoff freshness contract

## Problem

The zCloud roadmap requires a warning for a stale Notion handoff, but current
Notion page metadata does not prove when the **handoff itself** was written.
Using a page-level `last_edited` timestamp would create false confidence:
unrelated edits could make an old handoff look fresh.

## Contract

`scripts/zcloud_notion_handoff_freshness.py` therefore accepts only an
explicit durable handoff source with schema `notion-handoff-source-v1`.
Each active project must have one record containing:

- `source_kind=durable_handoff_event`;
- `handoff_at`, the timestamp attached to the handoff write itself;
- `recorded_at`, when that event was durably recorded;
- a bounded source-level `captured_at`.

Page-level `last_edited`, page age, or other inferred timestamps are
explicitly invalid and can never produce a fresh result.

## States

The read model emits only `fresh`, `stale`, `missing`, or `invalid`
plus bounded reason codes and age in seconds. It does not emit Notion page
content, page ids, prompts, handoff text, or raw source rows.

## Integration boundary

This PR is intentionally add-only. It does **not** add a Notion writer, modify
SQLite, change the dashboard, or reinterpret existing
`project_state_receipts` as Notion evidence. A later serialized integration
must create the durable event at the same success boundary as the real handoff
write and only then feed that source into this policy.

Until that exists, the correct live behavior is `missing`, not a guessed
freshness warning derived from page age.
