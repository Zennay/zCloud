# Cosmetic-refactor reliability admission contract

This policy implements the zCloud self-improvement guardrail:

> Geen cosmetische refactor zolang er onbehandelde P0/P1 reliability-issues zijn.

It is intentionally a pure admission decision. It does not query or mutate
GitHub, Notion, SQLite, queues, files, services or runtime state.

## Inputs

The caller supplies:

- a bounded machine change id;
- a change kind from the fixed vocabulary
  `cosmetic_refactor`, `functional`, `reliability_fix`, `security_fix`,
  `docs_only`, `test_only`;
- the canonical set of currently open **reliability** issues, each with a
  machine id and `P0`–`P4` priority.

Runtime integration must derive both the change classification and the issue
set from canonical evidence. A prompt or worker may not self-declare that an
otherwise cosmetic change is functional to bypass this policy.

## Decision

A `cosmetic_refactor` is rejected with
`RELIABILITY_WORK_REQUIRED` whenever at least one open P0/P1 reliability
issue exists. P2–P4 reliability debt does not trigger this specific rule.

Other change kinds are outside this guardrail and return `ALLOWED`; they
remain subject to all other zCloud gates such as migration, dependency,
blast-radius, coordination and safety policies.

## Bounds and privacy

Input is capped at 32 KiB and 100 reliability issues. File input must be a
regular non-symlink file. Unknown/hidden fields are rejected rather than
ignored. IDs are bounded machine values and duplicate issue IDs fail closed.

Output contains only the bounded change id/kind, fixed decision/reason code,
issue counts and bounded urgent issue IDs. No issue title, description, prompt,
patch, command or arbitrary evidence text is accepted or emitted.

## Integration boundary

After the serialized runtime-writer window is free, integration should:

1. query canonical open P0/P1 reliability ownership;
2. classify the proposed change from the actual changed-file/diff surface;
3. execute this policy before self-improvement admission;
4. persist only the bounded decision receipt;
5. fail closed if either evidence source is unavailable or stale.
