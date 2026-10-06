# Backlog impact/risk selection contract

This is the isolated policy foundation for the zCloud autonomy rule:

> Selecteer eerst één concrete backlog-item met de hoogste
> impact/laagste risicoverhouding.

The policy is deliberately pure. It accepts no prompt, patch, command, approval
or apply fields and performs no queue, GitHub, Notion, filesystem or VPS write.

## Input

`schema_version=1` and 1–100 candidate objects. Every candidate has exactly:

- `id`: canonical machine id;
- `impact`: integer 1–5, larger means more valuable;
- `risk`: integer 1–5, larger means riskier;
- `executable`: current work can actually be performed;
- `claimed`: another worker already owns it;
- `human_gate`: external/human input is required;
- `conflict`: current file/capability ownership conflicts.

Impact and risk are inputs to this policy; this contract does not invent them.

## Decision

Candidates that are non-executable, claimed, human-gated or conflicting are
excluded first. Among remaining candidates the exact rational
`impact / risk` score is maximized using integer fractions, not floats.

Ties use deterministic ordering:

1. higher absolute impact;
2. lower absolute risk;
3. lexicographically smaller candidate id.

When no candidate is eligible the result is `NO_ELIGIBLE`, never an unsafe
fallback.

## Safety

The input is capped at 64 KiB and 100 candidates. Numeric and boolean types are
strict: Python/JSON booleans cannot masquerade as integers, floats/strings are
rejected for scores, unknown fields are rejected, and IDs must be unique.

Output contains only the selected machine id, bounded score metadata, counts and
exclusion reason counts. Runtime integration must later obtain impact/risk and
eligibility from canonical evidence and re-check the coordination preflight
inside the actual claim transaction.
