# zSentinel · Private Product Delivery Workbench

The [source-bound product delivery board](zsentinel-product-delivery-board.md) is a deliberately non-authorizing, offline summary of existing P0/P1 issue discovery, queue retry pressure, recent PR-less branches, optional explicit Notion claims and exact-head PR observations. The new **Product Delivery Workbench** turns one saved JSON board into an interactive, still fully local operator workspace.

## Operator flow

1. Generate a private **JSON** board using existing merged CLI and the issue inbox once its exact-head PR is integrated. You may omit optional retry/branch/claim/PR source files, but missing evidence remains unknown.
2. Create the workbench from **that actual immutable board**, without touching live GitHub, Notion, SQLite or a VPS worker:

```sh
python3 scripts/zsentinel_product_delivery_board.py \
  --inbox ./issue-inbox.json --format json \
  --output ./my-delivery-board.json

python3 scripts/zsentinel_operator_workbench.py \
  --board ./my-delivery-board.json \
  --output ./my-private-delivery-workbench.html
```

3. Open the HTML locally, on mobile or desktop. Search and filter the *already observed* real issues by project or priority. Each card links to the original GitHub issue, its bounded existing PR references, known evidence gaps and the owner's recommended next investigation. Choose a **human-only disposition**: investigate, inspect/integrate existing PR, coordinate owner, park external blocker, or candidate for new product implementation (requiring independent fresh claim verification).
4. Add a private note for an issue. **Download private human handoff** to create a source-bound `zsentinel-human-handoff.json` in your browser. If you close the page, **Reopen own handoff** restores precisely that file only if the SHA-256 of the original board, observation timestamp, entire known issue inventory and read-only authorization flags match. Wrong, malformed, overlong or foreign handoffs leave the current notes alone. There is no automatic persistence, autosave, external API call or output to Notion.

## Source, privacy and safety

The workbench opens **one existing regular-file JSON board** via one bounded no-follow descriptor and hashes the **exact bytes that were validated**; a second path reopen cannot silently point its human handoffs to a different board. Duplicate JSON keys, nonfinite values, source mutation, wrong schema and unexpected authorizing flags fail closed. Every GitHub issue/PR link is reconstructed or validated against canonical GitHub-only paths. All visible strings are DOM text nodes, never dynamic `innerHTML`. The embedded JSON escapes HTML delimiters. A strict local Content Security Policy blocks network connections, forms, scripts outside the generated page, images and external resources.

The output HTML is create-only, mode 0600, using the existing board's atomic no-replace reporter. No old snapshot, source, symlink, other report or database can be overwritten. Public issue metadata is the only displayed source content; original queue task titles, prompts, raw lease IDs and credentials are never embedded. The operator's private notes stay in the browser memory until explicit download.

Most importantly: **no claim, merge, release, queue retry or deployment can be performed or authorized through this page.** A visible candidate PR is not proof of ownership, passed mandatory CI, adequate review or release consent; an empty claims snapshot is not a lease release. zSentinel's Notion status stays Verifying until actual required runtime and worker evidence exists. Do not publicize private handoff exports or invent recovery claims from this source-only UI.

No QA, regression or test suites were authored or manually run as part of this feature. The existing repository/owner/production gates stay independent.

## Portable offline project briefing (19 October product extension)

The Workbench now shows a **portfolio observation panel** calculated solely from the
already loaded, bounded JSON snapshot: observed P0/P1 issue totals, projects with
observed issues, referenced PR candidates, explicit evidence gaps, original
observation time and a warning when the saved observation is older than six
hours or its time is unreliable. A zero count is zero **within that captured
snapshot**, never proof that the live portfolio is clear.

Use the existing search, priority and project filters to select the exact issue
set for a handoff. After entering optional **human-only** disposition notes,
select **Preview current filtered briefing**. The complete Markdown preview
includes the original board SHA256, observation UTC time, canonical GitHub
issue and bounded PR links, issue-specific missing gates, next investigation
and any personal notes. Nothing is submitted to Notion/GitHub. The recipient
and the download directory are under the operator's control.

**Download exactly this private briefing** is offered only after previewing it.
Editing a note, switching the selected issue or changing any filters invalidates
the preview; export recreates the content from the current source/notes and
refuses a drifted document. A previously downloaded handoff can still be
restored through the original exact-board-hash-bound JSON path. The Markdown
is human-readable **not evidence of a live claim, security clearance, merge
authorization, valid runner or deployment**. Sensitive human notes may appear
in the file, so do not put it on a public issue or share it without review.

The extension uses only inline static UI code in the existing HTML output.
No live web browser automation, service route, SQLite, git action, worker
dispatcher, queue, new dependencies, QA suites or test runs were added.

## P0/P1 human triage queue and disposition filters (19 October)

The offline Workbench now exposes a **Next-action queue** below the snapshot
portfolio. It displays currently filtered P0/P1 issue cards plus lower-priority
items that the operator explicitly marked as **park-blocker**. The queue uses
the same local, bounded immutable source cards, sorts P0 before P1 and shows
the first available recorded evidence gap together with the **human-only**
disposition. Clicking one entry opens the existing issue details, its original
GitHub issue, candidate PR links and missing evidence. This is a *navigation
tool*, not a scheduler or admission gate.

The **Human disposition** filter works together with existing project, priority
and text filters and controls both the queue and the exact-source Markdown
briefing preview. The default "Investigate / not yet reviewed" includes cards
without a saved choice; **Candidate only** explicitly means "verify live
claims", never that a worker has already been assigned or may claim it.
Changing a disposition invalidates the current Markdown preview and updates
the displayed work. Imported private handoffs repopulate the same filters and
queue from their original board SHA. A missing P0/P1 card means only that the
saved snapshot/filter has none; not that the real GitHub portfolio is clear.

Neither action calls external APIs or modifies Notion, GitHub, SQLite, an
active lease, a runner, or production. The user must still independently check
real ownership, exact-head required CI, review, permissions and serialized
release approval. The source changes do not create or run QA test suites.
