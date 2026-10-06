# Evidence progress dimensions

zCloud already exposes one evidence-based project progress percentage. This contract
adds an optional second layer that can explain progress as **Research**, **Build**,
**Validation**, and **Operations** without inventing extra certainty.

## Evidence rule

A dimension percentage is the arithmetic mean of the existing milestone progress
values that can be assigned to that dimension. The classifier never derives a
percentage from time, message count, branch count, done=true, or a project phase
string.

Milestones with missing, boolean, non-finite, negative, or greater-than-100
progress values are not used.

## Classification

An explicit milestone dimension field is canonical. Supported values are research,
build, validation, and operations.

For the current registry, where older milestones do not yet carry that field,
scripts/zcloud_progress_dimensions.py has a conservative versioned keyword
fallback. It assigns a milestone only when exactly one dimension matches.

Zero matches become unclassified. Multiple matches become ambiguous. Neither
contributes to a dimension percentage. For example, “Beta deployment & real-user
validation” is both Operations and Validation, so the fallback must not choose one.

## Output boundary

The reporter emits project ID, available dimensions with evidence-derived
percentage and milestone count, aggregate classification counts, milestone
revision, progress basis, source filename, and source SHA-256.

It does not emit milestone titles. This keeps the read model bounded and avoids
turning internal milestone text into telemetry/log output.

## Dashboard integration boundary

This slice defines and proves the read-only contract only. It does not modify
server.py, public dashboard files, SQLite, scheduler state, browser automation or
live VPS configuration while the serialized control-plane integration window is
owned by PWQ-41/#580.

A later dashboard integration may consume this contract when it preserves these
rules:

1. show only dimensions whose status has evidence;
2. label dimension progress as derived from milestone evidence, not a new canonical total;
3. retain the existing overall evidence-progress metric as the project-level checkpoint;
4. surface ambiguous/unclassified coverage rather than silently forcing it into a category;
5. keep project-specific explicit dimension tags optional, so projects that do not support meaningful decomposition can remain unsplit.
