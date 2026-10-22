# zSentinel private operator bundle recovery

The private bundle command supports an explicit `--resume-partial` mode for interrupted multi-file publication. The default remains create-only and refuses any existing destination.

## Start or resume a bundle

Use the same saved source files, source order, output directory, bundle name, row limit, freshness window, and `--owner-handoff` choice as the original invocation. Add `--resume-partial` when retrying:

```sh
python scripts/zsentinel_operator_bundle.py \
  --inbox ./private/inbox.json \
  --output-dir ./private/bundles \
  --name incident-20261022 \
  --owner-handoff \
  --resume-partial
```

The example paths are placeholders. The output directory must already exist and be owned by the current user with no group/other permissions.

## What resume guarantees

- Before output publication, a hidden owner-only `.<name>.checkpoint.json` records exact source SHA-256/byte fingerprints, the selected options, the fixed UTC generation clock, and expected digests for every artifact and the final receipt.
- A retry is admitted only if all source bytes and output-affecting options match that checkpoint. The original generation clock is reused so freshness labels, HTML, Markdown, and receipt bytes remain reproducible.
- Existing outputs are opened without following symlinks and must be stable, regular files owned by the current user with mode `0600`; their bytes must match the checkpoint's expected output exactly.
- Missing outputs are created without clobbering existing files. The receipt remains the final published artifact. A failed retry preserves all existing files and the checkpoint.
- The checkpoint is retained as private recovery metadata. It is not a live GitHub/Notion observation, proof of current ownership, CI approval, merge authorization, or deployment evidence.

A partial bundle produced before checkpoint support, or one whose checkpoint, source bytes, options, or existing artifact contents do not match, is refused. The tool does not guess, delete, repair, or overwrite those files; choose a different bundle name after preserving the old artifacts.

No network requests, worker claims, queue writes, GitHub mutations, CI dispatch, merge, or deployment are performed by this command.
