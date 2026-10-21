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

## 21 October · file/directory conflicts in the merge-first workorder

The existing private PR workorder now carries file/directory ancestor conflicts separately from exact shared filenames. It shows the selected path, peer path, relation direction and the peer PR's observed HEAD. These rows come only from a stable bounded collision snapshot or a separately pinned focused-PR receipt, and the selected PR HEAD must still match the observation.

The CSV, offline HTML and Markdown owner handoffs expose the same path-pair relation. This helps an operator spot incompatible Git tree layouts such as a file at `src` versus a descendant `src/app.py` before changing either branch. It does not establish a Git merge conflict, owner, lease, fix, required-check set or merge authority. When the path-pair preview is capped, the workorder marks the source incomplete.

## 21 October · private multi-PR collision follow-up calendar

The existing **Issues met meerdere bestaande PRs** worklist now has a human-only calendar date per collision issue. Enter a local date to schedule a coordination check with the current PR owners. The page immediately labels future, today and expired follow-ups using **the viewing device's calendar date**. This is not a GitHub/Notion due date, a source timestamp or a release/SLA commitment.

Operators can **filter the issue, PR and multi-PR collision panels to expired private follow-ups**, navigate the next visible overdue coordination item, and download a separate **expired follow-up Markdown** handoff. The ordinary full, unresolved, single-issue, one-PR, project and issue handoffs carry the local follow-up date where that source issue is included. Hidden UI filters do not silently narrow the full-source export. The expiry count is a human triage hint, not proof of project inactivity.

The existing opt-in, source-token-bound private JSON session persists the date alongside each collision issue's coordination state and note. Imported dates must be canonical calendar dates within 2000–2100, and a malformed/foreign session is rejected before any local edits are applied. Earlier v2 sessions without dates restore with blank dates; v1 issue-only sessions preserve their original compatibility semantics. Never upload these private notes and dates to a public issue without the owner's deliberate consent.

**Authority boundary:** no GitHub request, Notion edit, current claim transfer, CI retry, runner/service command, merge, deployment or automatic notification is made by this browser workspace. The source can be partial/stale and a shared issue never proves overlapping files or duplicate fixes. Existing exact-head CI, independent review, branch protection and operator release gates are unchanged.

## Private calendar handoff for open PR coordination

The offline multi-PR collision panel can export locally scheduled, unresolved follow-ups as an RFC 5545 `.ics` calendar file. Each valid, open collision issue with a private follow-up date becomes an all-day calendar item with a stable issue-based UID, source issue link, observed local coordination status and a note that the date is **not** a GitHub deadline or SLA. The event title and description also list up to eight canonical linked PR candidates from the bounded saved source, clearly labelled as candidates (a shared issue does not prove overlapping files or duplicate fixes). It also includes the saved issue/PR source timestamps and explicitly instructs the operator to recheck current claims, exact PR HEAD, required CI, independent review and branch protection before acting. Unicode calendar lines are folded by UTF-8 octet length; ICS text separators and newlines are escaped.

Only the date and public source fields are included. Private operator notes are intentionally omitted. Items already marked as locally followed up, entries without a valid date, and non-canonical GitHub issue URLs are excluded. The export is a local download only; it does not create calendar events through an external API, write back to GitHub/Notion, or authorize a claim, review, merge or release.

## 21 October · direct te importeren merge-first issue/PR werkbak als privé-CSV

De bestaande `scripts/zsentinel_issue_scope_cockpit.py` accepteert
`--format worklist-csv` met `--worklist-limit 1..60`. De export gebruikt
exact dezelfde reeds verzamelde, begrensde GitHub- en opgeslagen brondata
als de issue-first werkbak. Er wordt **geen extra provider-opvraag,
claimcontrole, CI-run of dispatch** gestart door de CSV-renderer.

```bash
umask 077
python3 scripts/zsentinel_issue_scope_cockpit.py \
  <dezelfde reeds gebruikte bronopties> \
  --format worklist-csv --worklist-limit 60 \
  --output /secure/private/zsentinel-merge-first-worklist.csv
```

De CLI verwerkt de bronopties identiek aan zijn bestaande HTML-uitvoer.
De CSV bevat **één rij per unieke bestaande PR als eerste groep**, gevolgd
door P0/P1/P2-issuekaarten in bestaande prioriteitsvolgorde. Eén PR met
meerdere genoemde issues veroorzaakt nooit meerdere fictieve
implementatietaken. Per rij zie je bronlinks, geobserveerde PR-HEAD (of
leeg/UNKNOWN), gate-status als *bronwaarneming*, PR-/issue-moment, relatieve
source-coverage en één menselijke vervolgstap. Als `--worklist-limit` een
deel van de bron verbergt blijft dat expliciet gelabeld; de export claimt
niet alle open branches, actuele eigenaren of PRs te zien.

Titels en menselijke velden worden beperkt tot één cel, op Excel/Sheets
CSV-formuleaanzet gecontroleerd (ook na onzichtbare Unicodeprefixes) en met
correcte CSV-escaping gequote. Het bestand gebruikt de bestaande
create-only `0600` privéschrijfroutine. Publiceer de privé-CSV niet
automatisch in GitHub-issues of chatlogboeken.

**Vrijgavegrens:** alle rijen hebben expliciet
`current_owner_verified=NO`, `claim_authorized=NO`,
`merge_authorized=NO`, `deploy_authorized=NO`. Deze offline werkbak
is een menselijke overdracht; exacte actuele GitHub-HEAD, verplichte
CI/branchregels, onafhankelijke review en Notion-/worker-eigenaarschap
moeten nog apart gecontroleerd worden. Een geobserveerde groene check
is geen merge-toestemming.


### Eénknops-CSV vanuit dezelfde offline HTML-werkbak

De bestaande `--format worklist-html` cockpit heeft nu ook de knop
**Download PR + issue werkbak (CSV)**. Het bestand wordt gemaakt uit
dezelfde gevalideerde, brongebonden werkbak die de CLI met
`--format worklist-csv` exporteert, inclusief de beperkte zichtbare
issuebron en unieke PR-groepen. Het exact ge-escape'te CSV-resultaat wordt
als base64 in het bestaande lokale HTML-bestand opgenomen en uitsluitend
via een bewuste knopklik als lokale Blob gedownload. De pagina doet geen
nieuwe provider-read of online request en stuurt geen private operatornotities
naar een externe dienst.

De CSV bevat **waargenomen GitHub-bron**, niet de mogelijke lokale
notities/afstemmingsbesluiten uit de HTML-sessie. Een ingeschakeld zoek- of
projectfilter verandert de oorspronkelijke opgeslagen CSV-bron niet.
Het blijft een privé-export zonder eigenaars-, claim-, CI-, merge- of
deployautorisatie. De gebruiker moet bij ieder besluit eerst de live
GitHub- en Notion-bron en verplichte releasevoorwaarden controleren.
