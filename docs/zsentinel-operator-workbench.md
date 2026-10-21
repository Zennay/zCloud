# zSentinel · Private Product Delivery Workbench

The [source-bound product delivery board](zsentinel-product-delivery-board.md) is a deliberately non-authorizing, offline summary of existing P0/P1 issue discovery, queue retry pressure, recent PR-less branches, optional explicit Notion claims and exact-head PR observations. The new **Product Delivery Workbench** turns one saved JSON board into an interactive, still fully local operator workspace.

## 21 oktober · projectgerichte issue-/PR-botsingswerkbak uit privé bronmomenten

De bestaande historische, issue-eerst dossier-CLI heeft nu een **gerichte
menselijke herstelwerkbak**. Dezelfde 1–12 eerder veilig geopende privé-ZIP
bronmomenten blijven de enige bron. Kies een project en een historisch
aandachtspunt zonder actuele rechten of status te suggereren:

```sh
python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./saved-older.zip --source ./saved-newer.zip \
  --project Zennay/zCloud --attention multiple-prs \
  --format html --output ./private-multiple-prs.html

python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./saved-older.zip --source ./saved-newer.zip \
  --project Zennay/zCloud --attention lost-issue-link \
  --format zip --output ./private-lost-link-handoff.zip
```

De drie aandachtspunten zijn `multiple-prs` (meer dan één bestaande PR
noemt hetzelfde issue in de opgeslagen waarnemingen),
`lost-issue-link` (een eerdere issuekoppeling verdwijnt uit de
eindsteekproef, ook als de PR nog wel wordt bemonsterd), en
`missing-final-pr` (een eerder bemonsterde gekoppelde PR ontbreekt
in de laatste beperkte steekproef). **Geen** van deze labels bewijst
bestandsconflict, claimvrijgave, gesloten issue, merge of actieve eigenaar.

`--project` accepteert uitsluitend een canonieke `Zennay/<repo>` scope.
`--priority P0|P1|P2|Unspecified` kan gecombineerd worden met de
aandachtsfocus en bestaande begrensde paginering. `--inspect-issue`
blijft exact één bekend canoniek issue vereisen en is niet te combineren
met een aandachtspunt. Een gefilterd projectdossier laat geen
ongekoppelde PRs van andere projecten zien; bij focus worden
ongekoppelde PRs helemaal weggelaten, nooit als 'afgehandeld' gemarkeerd.

In de HTML en Markdown staat bovendien **vóór de gekozen focus** een
historische aandachtsteller voor de volledige geselecteerde projectscope:
meerdere PR-kandidaten, verloren issueverbindingen en uit de laatste
steekproef verdwenen PRs. Deze aantallen mogen overlappen en zijn nooit
een actuele CI-/merge-/claimstatus. Bij projectselectie tonen ongekoppelde
PRs uitsluitend die projectscope; het oorspronkelijke totaal blijft apart
zichtbaar, zodat filtering geen vals nulresultaat suggereert.

De selectie is in de HTML en Markdown zichtbaar en wordt in JSON/CSV
opgenomen; bij ZIP staan exact die geselecteerde HTML/Markdown/CSV/
bronreceipts plus SHA256-checksums in het bestaande private archief.
De nieuwe ZIP-manifestvelden `selected_project`,
`historical_attention_focus`, `selected_issue_count`,
`matched_issue_count_in_selected_scope` en
`source_total_issue_count` maken de **weggelaten historische
steekproefscope** expliciet. Alle bestandscreatie blijft alleen op een
nieuwe privé bestemming (O_EXCL, O_NOFOLLOW, 0600, groottebudget).
Geen live API, Notion-/lease-/queuewrite, merge, deploy of CI-retry.

### Actualiteit van opgeslagen issue-bronnen

De issue-eerst CLI beoordeelt de leeftijd van de **laatste opgeslagen waarneming**
en neemt die beoordeling mee in HTML, Markdown, CSV, JSON en de private ZIP
receipt. De standaard stale-drempel is zes uur; kies desgewenst een begrensde
drempel van 1–168 uur:

```sh
python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./saved-older.zip --source ./saved-newer.zip \
  --project Zennay/zCloud --attention multiple-prs \
  --stale-after-hours 6 --format html --output ./private-multiple-prs.html
```

De status is `recent-snapshot`, `stale`, `clock-skew` of `unknown`.
Een recente snapshot blijft historische brondata, geen actuele GitHub-status.
Een verouderde, ontbrekende of meer dan vijf minuten in de toekomst liggende
bronwaarneming vraagt expliciet om nieuwe broninspectie vóór een beslissing.
Het evaluatiemoment UTC, de laatste waargenomen UTC-tijd en de drempel worden
vastgelegd; de oorspronkelijke bronmanifest-hashes blijven ongewijzigd.
Dezelfde optie werkt ook op de historische PR-tijdlijn:

```sh
python3 scripts/zsentinel_saved_dossier_timeline.py \
  --source ./saved-older.zip --source ./saved-newer.zip \
  --stale-after-hours 12 --format markdown --output ./private-pr-history.md
```

Beide CLI's gebruiken nu dezelfde gedeelde tijdstempelregels en leggen
status, leeftijd, evaluatie-UTC en gekozen drempel vast in hun output.
De vergelijkende portfolio-inbox geeft de actualiteit van beide bronmomenten
naast elkaar weer en gebruikt dezelfde begrensde drempel. Ook een dossier voor
één exact geselecteerd issue gebruikt diezelfde status, leeftijd en evaluatieklok:

```sh
python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./saved-older.zip --current ./saved-newer.zip \
  --stale-after-hours 6 --format html --output ./private-issue-delta.html

python3 scripts/zsentinel_saved_issue_delta.py \
  --previous ./saved-older.zip --current ./saved-newer.zip \
  --issue https://github.com/Zennay/zCloud/issues/1326 \
  --stale-after-hours 6 --format markdown --output ./private-single-issue.md

python3 scripts/zsentinel_saved_issue_archive.py \
  --input ./private-issue-handoff.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1326 \
  --stale-after-hours 6 --handoff-markdown ./private-archive-handoff.md
```

Geen filter, versheidslabel of ZIP-receipt bewijst eigenaar, claimvrijgave,
issue-sluiting, verplichte CI, review, merge of live herstel.

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

### Per issue overdragen zonder de rest van het portfolio te delen

De geselecteerde issue kan als afzonderlijke `zsentinel-private-single-issue-handoff` JSON worden geëxporteerd. Deze bevat uitsluitend de gevalideerde bronvelden voor die ene issue, de bijbehorende waargenomen PR/gate-verwijzingen en de lokale menselijke opvolging. Andere issuekaarten, projectbrede wachtrijsignalen en notities blijven buiten het bestand. Bekijk de preview vóór download.

Import accepteert alleen een bestand van exact dezelfde board-SHA en waarnemingstijd en vergelijkt de volledige issuebron opnieuw. Bevestigen wijzigt uitsluitend de menselijke opvolging van die ene issue in het huidige browsertabblad. Bestaande afwijkende notities worden niet overschreven; gebruik voor een oudere board de bestaande expliciete carry-forward-flow. De import voert geen GitHub-/Notion-call uit en geeft geen claim-, CI-, merge- of deploybevoegdheid.

## 21 oktober · kruisverwijzingen naar andere PRs bij dezelfde bronissues

Bij `portfolio-pr-markdown`, `portfolio-pr-html` en
`portfolio-pr-zip` toont één geselecteerd bestaand product-PR-dossier
voortaan ook de andere PR-kandidaten die **volgens het oorspronkelijke,
volledig gevalideerde beperkte opgeslagen manifest** hetzelfde bronissue
noemen. Dit helpt de bevoegde eigenaar een overlappende PR-review niet
over het hoofd te zien.

- De bron wordt eerst als geheel toegelaten, dan wordt de canonieke
  geselecteerde PR geïdentificeerd; per gekoppeld issue worden uitsluitend
  kandidaten uit **dezelfde repository** met diezelfde bronissue-URL
  overgenomen. Elke andere PR houdt zijn eigen canonical URL, bronprioriteit,
  gatebron en al dan niet bekende geobserveerde HEAD. De overige projecten
  of PRs buiten de bron blijven **ONBEKEND**. Een gedeeld issue bewijst
  niet dat beide PRs dezelfde bestanden veranderen of de issue fixen.
- Markdown vermeldt de alternatieve PRs direct onder de oorspronkelijke
  issuevermelding, HTML toont mobiele geneste bronlinks. Als de huidige
  beperkte steekproef geen ander PR toont, betekent dat **niet** dat
  de live repository vrij van andere PRs of claims is.
- De private ZIP bevat aanvullend het **machineleesbare**
  `linked-peer-prs.json`. Dit bestand koppelt ieder bronissue aan
  zijn andere geobserveerde PRs met hun bron-HEAD/gate en expliciete
  `claim_authorized=false`, `merge_authorized=false` en
  `complete_current_pr_inventory_verified=false`. Het bestaande
  `source-checksums.json` bindt ook deze nieuwe bijlage aan SHA256.
  `source-receipt.json` bevat het aantal unieke andere PRs uit die
  bron. De bestaande write/file/size safety blijft gelijk.

Deze functie opent geen GitHub/Notion API, leest geen nieuwe live claims
en voert nooit review, CI, merge, deployment of queue-mutatie uit.

## Nieuw: één bestaande PR als privé eigenaarsoverleg exporteren

Voor een concreet openstaand product-PR hoef je niet langer een volledige
portfolio-export te delen. De bestaande offline bronbridge biedt nu
`--format portfolio-pr-markdown --pr-url`,
`--format portfolio-pr-html --pr-url` én
`--format portfolio-pr-zip --pr-url`: alle drie valideren eerst **het
complete oorspronkelijke manifest en alle reeds opgeslagen batchbewijzen**,
zoekt vervolgens één canonieke PR in die bron en maakt een afzonderlijk,
brongebonden privé-Markdown-dossier. Een niet-waargenomen PR geeft een fout,
niet een "geen problemen" of reeds gemergede status.

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review.json \
  --receipt ./private-gates-01.json \
  --format portfolio-pr-markdown \
  --pr-url https://github.com/Zennay/zCloud/pull/1560 \
  --output ./private-pr-1560-owner-handoff.md
```

Bij meerdere vooraf opgeslagen batches herhaal je `--receipt` zoals in het
bestaande bridgecontract; ontbrekende batches zijn alleen met de bestaande
expliciete `--allow-partial` optie toegestaan en blijven **UNKNOWN**.
Het optionele `--project Zennay/zCloud` moet bij de bron-PR passen.
`--pr-url` is uitsluitend toegestaan bij de drie
`portfolio-pr-markdown`/`portfolio-pr-html`/`portfolio-pr-zip`-formaten;
alle andere formaten
behouden hun eerdere invoercontract. Voor een **mobiel leesbare, scriptvrije
HTML-pagina** gebruik je dezelfde invoerbronnen en vervang je in bovenstaand
commando alleen `--format portfolio-pr-markdown` door
`--format portfolio-pr-html`, en de uitvoernaam door
`./private-pr-1560-owner-handoff.html`.

De offline HTML-pagina geeft de PR, oorspronkelijke issue-links,
waargenomen broncommit, CI-/reviewgebreken, broninterval, ontbrekende batches,
bron-SHA256's en de menselijke vervolgactie in aparte toegankelijke kaarten.
Een strenge Content Security Policy verbiedt scripts, netwerkverzoeken,
afbeeldingen en formulieren; externe GitHub-links openen alleen na een
vrijwillige klik. Printweergave blijft leesbaar.

Wie één brongebonden dossier met zowel HTML als Markdown als
één privébestand wil overdragen, gebruikt ook
`--format portfolio-pr-zip --pr-url https://github.com/Zennay/zCloud/pull/1560`
en bijvoorbeeld `--output ./private-pr-1560-owner-handoff.zip`.
De ZIP bevat vaste namen (`single-pr-dossier.html`,
`single-pr-actions.md`, `source-receipt.json` en
`source-checksums.json`). De checksum-index bindt de drie oorspronkelijke
inhoudsbestanden aan SHA256 en het exacte reviewmanifest. Alle ZIP-leden
hebben vaste veilige metadata en 0600-permissies. Het bestaande create-only
0600/no-follow/bounded-output-contract wordt niet vervangen.

Het bestand bevat de exacte originele reviewmanifest- en board-SHA256,
UTC-broninterval, ouderdom bij export, alle batchdekkingsgaten,
de waargenomen bron-HEAD en bijpassende onveranderlijke commitlink, actieve
gate-waarnemingen uit de opgeslagen bron, gekoppelde issue-URLs en een
**menselijke vervolgstap**. Het bestand meldt uitdrukkelijk dat de huidige
PR-status, eigenaar, vereiste CI, review, beschermd `main` en productie
**niet** door deze historische bron zijn bevestigd.

Bestaande create-only `0600`-output, maximale bestandsgrootte,
no-follow write en toestemminggrenzen van de bridge blijven ongewijzigd.
Dit is geen nieuwe CI-run, netwerkscanner, workerclaim, merge- of deployroute.
De gebruiker moet de huidige GitHub-/Notion-bewijzen apart controleren.

## Merge-first: exact waargenomen bron-HEAD en ontbrekende gatebewijzen

De bestaande opgeslagen PR-batch kan nu in de **projectpagina** én de
**issue→PR-inbox** rechtstreeks verwijzen naar de **waargenomen commit-SHA**.
Elke bestaande PR-kaart toont de bron-HEAD (of expliciet `ONBEKEND`), een
klikbare commitlink indien volledig gevalideerd, en een aparte
bronintegriteitssamenvatting. De volgende PR per project krijgt die informatie
ook buiten de uitklapbare lijst, zodat de operator onmiddellijk ziet waar het
onderzoek kan beginnen.

Dezelfde broninformatie gaat mee naar de **project Markdown-handoff** en de
**per-PR CSV-export** als `Bronintegriteit` / `Bron gate integriteit`:
compleet of onvolledig ontvangen checkpagina, mogelijk afgekapt review-/legacy
statusvenster, waargenomen wijzigingsverzoeken en legacy pending/failed
signalen. Bij ontbrekende gatebronnen is de HEAD **onbekend**, niet groen.
De bron-SHA betekent alleen *destijds waargenomen broncommit*; de actuele HEAD,
voldoende verplichte CI, onafhankelijke review, actieve claim, werkelijke merge
en live status blijven expliciet **apart** door de bevoegde operator te
bevestigen.

De volledige lokale projectpagina toont bovendien de ouderdom van de bron
**op het moment van genereren**. Na zes uur is dit een duidelijke
`VEROUDERDE BRON`-melding; ook een jongere bron wordt uitdrukkelijk *niet live*
genoemd. Deze melding verschijnt ook in het project-Markdown-overzicht en in
zowel project- als per-PR-CSV, zodat een geëxporteerd overzicht geen stilzwijgend
tijdloos mergebewijs lijkt. De oorspronkelijke bron-UTC en beide
manifest-digests blijven behouden.

Deze wijzigingen lezen uitsluitend bestaande, begrensde offline brondata en
genereren lokale HTML/Markdown/CSV. Ze voeren geen GitHub-GET uit tijdens het
tonen, claimen geen werk, starten geen CI en autoriseren geen merge of deploy.
Er zijn geen QA-, regressie- of testsuites toegevoegd of handmatig gedraaid.

## Source, privacy and safety

The workbench opens **one existing regular-file JSON board** via one bounded no-follow descriptor and hashes the **exact bytes that were validated**; a second path reopen cannot silently point its human handoffs to a different board. Duplicate JSON keys, nonfinite values, source mutation, wrong schema and unexpected authorizing flags fail closed. Every GitHub issue/PR link is reconstructed or validated against canonical GitHub-only paths. All visible strings are DOM text nodes, never dynamic `innerHTML`. The embedded JSON escapes HTML delimiters. A strict local Content Security Policy blocks network connections, forms, scripts outside the generated page, images and external resources.

The output HTML is create-only, mode 0600, using the existing board's atomic no-replace reporter. No old snapshot, source, symlink, other report or database can be overwritten. Public issue metadata is the only displayed source content; original queue task titles, prompts, raw lease IDs and credentials are never embedded. The operator's private notes stay in the browser memory until explicit download.

Most importantly: **no claim, merge, release, queue retry or deployment can be performed or authorized through this page.** A visible candidate PR is not proof of ownership, passed mandatory CI, adequate review or release consent; an empty claims snapshot is not a lease release. zSentinel's Notion status stays Verifying until actual required runtime and worker evidence exists. Do not publicize private handoff exports or invent recovery claims from this source-only UI.

No QA, regression or test suites were authored or manually run as part of this feature. The existing repository/owner/production gates stay independent.

## Snelle offline triage: navigeren zonder claims

De bestaande private Workbench heeft nu direct onder de filters een vaste
**Vorige issue / Volgende issue / Volgende open P0/P1-opvolging** bediening.
Met **J** (volgende) en **K** (vorige) kan de operator dezelfde gefilterde,
brongebonden issues doorlopen. In tekstvelden, selecties en notities zijn die
toetsen volledig uitgeschakeld zodat ze het typen nooit verstoren.

- Volgende/vorige circuleren alleen over de **zichtbare gefilterde**
  momentopname en tonen positie plus bronissue. Geen filterkoppeling of echte
  claim wordt gewijzigd.
- In de bestaande **PR-desk** kan elke issuekoppeling ook rechtstreeks
  binnen de lokale werkbank worden geopend via **View issue in local
  workbench**. De eerdere privé-invoer wordt eerst lokaal bewaard, de
  zichtbare issuekaart springt in beeld en een oude exportpreview vervalt.
  De bestaande externe GitHub-issue-/PR-links blijven als aparte,
  vrijwillige navigatie beschikbaar. Er worden geen nieuwe API-verzoeken
  uitgevoerd om deze brongebonden koppeling te tonen.
- De P0/P1-sneltoets in de UI selecteert uitsluitend een urgente,
  **niet lokaal gesloten** menselijke opvolging. Een lokaal gesloten
  follow-up is géén gesloten GitHub-issue en sluit het issue niet uit van de
  gewone lijst.
- Bij elke overgang worden eventuele handmatig ingevoerde notities eerst
  in het bestaande **lokale handoff-geheugen** bewaard en wordt een eerder
  gemaakte privé-exportpreview ongeldig. Voor definitieve bewaring blijft
  expliciete JSON/Markdown-export nodig; geen browserstorage of autosave.
- Als een filter geen kandidaten meer toont, blijven huidige GitHub-status,
  actief eigenaarschap, verplichte CI, review en releaseautoriteit onbekend.
  Geen automatische bronopvraag, taakclaim, workeractie of merge.

De nieuwe navigatie is expliciet **lees- en bedieningsfunctionaliteit voor
het bestaande lokale product**, geen scheduler en geen QA- of testsuite.

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

## Offline two-observation change detection (19 October)

After generating a **new** workbench from a real current board, load an
**earlier JSON delivery-board** with *Earlier source snapshot*. The HTML
reads the file locally (maximum 2 MiB), validates the expected read-only
schema, per-card GitHub issue/PR identities, bounded paths and the earlier
timestamp, then pins its exact bytes with SHA-256. It refuses the same SHA,
future/tied/later observation, changed authorizing flags, duplicate issue IDs,
oversized cards, malformed IDs and unsupported evidence. There is no network,
GitHub poll or persistent storage. Use **Clear comparison** to forget it.

The comparison distinguishes **newly observed** issue cards, cards with a
changed priority, issue title, recommended investigation, observed PR set or
exact evidence-gap contents, cards still observed,
and cards **no longer in the new bounded sample**. The last category is
explicitly *not resolved*: a changed collector scope, pagination, GitHub
permissions, rate limit, project selection or stale observation could all
cause disappearance. No historical branch, issue or worker is automatically
considered available, integrated or recovered. Newly observed P0/P1 cards
are surfaced first *within their priority* in the human action queue. With
no earlier board, the original static triage order is preserved.

When a previous board is loaded, the reviewed **private Markdown briefing**
includes both source identities and a separate portfolio-wide comparison.
This historical section is intentionally portfolio-wide even if the visible
issue list has a narrower human disposition/project filter; its scope is
named explicitly, and it contains only bounded canonical GitHub issue IDs.
Inspect the complete preview before downloading, especially before sharing
a private portfolio snapshot outside the operator's environment. Importing
or clearing a comparison invalidates an earlier preview.

This feature is a source-observation comparison, **not** a current-state
monitor. There are still no background updates, recovery decisions,
CI dispatches, privileged queue operations, new test suites or runtime
deploys. Existing source/CI/independent owner/release gates remain in force.

### Source-coverage changes are visible, not silently interpreted

The current generated workbench includes only the canonical **bounded
coverage summary**, never GitHub raw response objects: project
partial/degraded/unavailable labels, inbox pagination truncation, card-count
truncation and source denial. It validates these before rendering. Earlier
source imports must carry valid equivalent labels. The comparison explicitly
shows both original capture scopes and flagged unavailable/degraded projects
alongside the two SHA-256 source identifiers.

All original zSentinel issue-inbox windows are partial, even if the
truncation/denial flags are false. Therefore *no longer observed* must never
be relabeled as *closed*, *resolved*, *delivered* or *free for assignment*.
The historical comparison does not have live issue/PR/claim authority and
cannot replace fresh GitHub+Notion owner checks.

### Original issue titles in the delivery cockpit

The board already contains bounded original GitHub issue titles, but the
Workbench previously displayed only opaque repository issue IDs. The
current cockpit now renders the **original public issue title** beside
the canonical ID in the priority queue, issue list and detail view, and
includes it in local text searches and previewed private Markdown
handoffs. Titles must be nonempty, at most 180 characters and validated
before the page is generated. UI strings are inserted only as DOM text;
the Markdown preview uses the same length/control/HTML-delimiter cleaning
as operator notes. Titles never establish assignee or action authority.

### 20 October · Evidence-aware incident changes

The previous comparison counted missing evidence items rather than their
actual contents. Replacing an owner/gate blocker with a different one of
the same count appeared as `still-observed`, so the operator could miss
an actionable change. The workbench now preserves a bounded, validated copy
of prior issue title, recommended investigation, referenced PR set and exact
missing-evidence strings **in memory only**. The historical comparison marks
changes to any of those fields (plus priority) and names precisely which
fields moved in the issue list and source-pinned Markdown briefing.

Earlier imports reject blank/overlength titles, overlength recommendations,
and linked PRs outside the card's original GitHub repository. This avoids
promoting a foreign link from untrusted offline input. A changed/vanished
evidence requirement is **not** proof the gate passed, nor does a newly linked
PR prove completion; every action still requires fresh actual ownership,
required CI, reviewer and release consent. No QA/regression suite was added
or run, and nothing contacts or writes to GitHub, Notion, workers or the VPS.

### Three-line human blocker handoff (20 October)

Every currently selected issue now exposes a **read-only three-line source card**:
original priority/issue/title; bounded next investigation (not an authorized
command); and the first observed missing evidence requirement plus the original
UTC observation. Operators can select these lines for a manually reviewed copy.
The view does not auto-copy to the clipboard, submit a request, include private
human notes, assign a claim, restart a runner or signal a recovery. A first
missing-evidence item is **not** an exhaustive gate inventory; review the full
issue and its original source links before acting. Missing evidence, even when
absent from the snapshot, never means that security/CI/review/release passed.

The three-line summary is regenerated when the issue selection changes; it is
not a continuously updating live monitor. This feature remains offline and
works entirely in the existing file-based operator HTML with no new service,
permission, QA/test suite, dependency, privileged write or release gate change.

### Change-focused triage and briefing filter (20 October)

With a valid earlier board loaded, the operator can now filter the **current**
observed issue list, human next-action queue and private Markdown briefing to:
newly observed issues, materially changed evidence (priority, issue title,
recommended investigation, attached PRs or exact missing-gate strings), or
still-observed issues. **New or changed evidence** combines the first two
categories for a focused delivery handoff. The filter is disabled without a
valid earlier board; clearing the previous board resets it to all currently
observed issues, rather than displaying a misleading empty shortlist.

The historical comparison still lists issues no longer visible in the current
bounded sample separately, and never declares them resolved or reassigned.
Changing the filter invalidates an older briefing preview; only a fresh,
reviewed preview can be downloaded. This remains an offline, read-only human
workflow without queue ownership or release permission. No testsuites were
written or run for this source feature.

## 20 October · Private human follow-up board (product delivery)

The Workbench now has a **Human follow-up · private, manual only** panel for
issues explicitly marked **Coordinate owner** or **Parked blocker**. Operators
may add a human contact **label** (80 characters maximum) and a UTC calendar
**review date** on the issue detail. These values are strictly local human
annotations, **not** verified Notion/GitHub assignees, task leases, delegated
authority, workflow permissions or actual delivery acknowledgements.

The follow-up panel groups human-marked cases by their operator-selected
dates and shows an explicit count of missing contact/date and overdue review
dates. "Overdue" only means that the UTC date has passed, **not** that GitHub
CI, a runner, a human owner or production failed after the observation. The
priority queue keeps P0 before P1 while surfacing overdue manual follow-ups
within a given priority. Its source card still requires fresh owner/CI/review
checks. There is no push/email/calendar/watch notification or automatic claim.

Use the **Follow-up status** filter to focus on: past review dates, missing
contact/date, due today UTC or scheduled later. The same selection applies
to issue navigation, blocker queue and private Markdown briefing. The
unfiltered private follow-up panel remains a comprehensive view of *manual*
human annotations for all bounded cards on the current source board.

Private handoff JSON has **schema_version 2** for contact labels and UTC
review dates. Older schema_version 1 handoffs still restore with empty
contact/date fields; neither version is accepted unless its original source
SHA-256 and observation timestamp match this exact offline board. Imported
v2 dates must be real calendar dates in 2020–2099 and contact labels must
remain bounded without control characters or unsafe HTML delimiters.
The UI sanitizes locally typed contact labels before export. Markdown
previews show the manually selected contact/date and distinguish escalation
from ordinary issues; all text is locally sanitized and the export must be
reviewed again after any edit/filter change.

Contact labels and notes can include private information. Keep exported
JSON/Markdown private and share only through an intentional human action.
No external webhook, browser API call, service, new database, automated
watch, worker dispatch, runner retry, QA suite or security gate change was
introduced. Source-level browser JavaScript was syntax-inspected without
running a QA/regression suite. Integration still requires original exact-head
automatic CI, independent review when required and protected-main/release
authorization. No source merge or production deployment is implied.

## 20 October · Explicitly reviewed offline calendar transfer for human blockers

The private workbench includes an **optional iCalendar (.ics) handoff** for
human-only follow-ups. Only saved issue decisions `coordinate-owner` and
`park-blocker` with **both** a locally entered contact label and real UTC
review date qualify; other product issues are excluded. The user must first
click **Preview reviewed calendar events** and inspect the entire generated
calendar text before **Download exactly these all-day events** becomes
available. Changing any filter, issue detail, human note, follow-up label or
date invalidates the preview; export regenerates from the same source state
and refuses changed content.

Events use stable SHA256-board-bound, canonical issue identifiers, a UTC
creation timestamp, a date-only `DTSTART;VALUE=DATE`, original canonical
GitHub URL and a short explicit "human review only" description. Values
are escaped and long lines are folded at the standard 75-octet boundary.
Export includes **no private free-form operator notes**, no calendar account
identifier and **no embedded VALARM**. The user chooses whether/where to
import this file. External calendar apps may apply their own default alerts,
which should be reviewed before saving. No automatic reminder, external
delivery, watch integration, GitHub write, owner assignment, release, CI
rerun, or background schedule is performed by zSentinel.

The ICS is private because contact labels could identify a person. Keep it
off public issue comments. A scheduled date is never proof of notified or
accepting ownership, and an expired date is not proof of failed CI.
Required live claim checks, exact-head CI, security authorization, review
and original deployment gates remain independent. No QA/regression suite
was authored or manually run.

## 20 October · Reviewed carry-forward between observed source boards

**Use case:** the operator has a newer saved zSentinel board and wants to
preserve a human contact or follow-up date from an older observation. Exact
SHA-pinned `Reopen own handoff` deliberately still rejects files from another
board. To migrate explicitly and safely:

1. Open the new/current workbench, load the actual earlier delivery-board JSON
   using **Earlier source snapshot**. The page hashes its exact bytes,
   validates its bounded source schema and requires its observation time to
   predate the current board.
2. Under **Reviewed human follow-up carry-forward**, choose the private
   handoff exported from **that same earlier board**. Its saved SHA-256 and
   observation timestamp must both equal the validated earlier source, and
   every referenced issue must be canonical and present in that earlier board.
3. Read the proposed transfer list and its counts. Only human decisions
   `coordinate-owner` / `park-blocker` with the **same canonical issue and
   project** on both boards are eligible. Old cards that vanished from the
   bounded new snapshot are omitted, not marked resolved.
4. Click **Confirm carry-forward of reviewed human follow-ups**. It transfers
   only annotations on current cards that have no meaningful manual
   disposition, note, contact, date or previously imported provenance. Existing
   current edits remain intact. Any intervening edit of a candidate prevents
   import until a fresh preview.
5. Reverify each imported human contact, GitHub issue, live Notion claim,
   required CI and release gate. Every imported entry displays the prior
   source SHA prefix; private JSON and Markdown exports preserve its original
   SHA-256 source provenance. This is **not** proof that the old assignee is
   still responsible or that a previously overdue issue is still open.

Earlier v1 JSON handoffs are accepted with empty contact/due dates. V2 JSON
handoffs may contain bounded UTF-8 human notes, contact/date and optional
prior-source metadata. Invalid, duplicate, cross-repository or unrecognized
cards fail closed rather than silently creating new current issues. The
whole flow runs locally in the browser without server writes, browser
network calls, worker assignment, notifications, retries, or queue mutations.
No QA/regression suites were created or run. Integration of this feature
remains gated by the existing protected-main and exact-head release checks.

### Historical source-drift visibility before manual import

Carry-forward preview compares **the old board's exact issue evidence** against
the new board, including priority, original title, linked PRs, recommended
investigation and individual missing-gate contents. It explicitly counts
cards with changed source evidence and identifies the changed fields beside
each proposed human annotation. This warns operators before they reuse a
contact or a date for an issue whose blockers may have materially changed.

An optional locally generated calendar event also includes the previous
board's SHA256 when its manual annotation was carried forward, plus a
renewed-owner-review warning. This is provenance only, not a verified
owner, released claim or calendar notification. The transfer still requires
a deliberate preview and confirmation; current human notes remain untouched,
and all original required source/CI/security/review/release gates stay active.

## 20 October · Local closure and reopening of manual follow-ups

The operator can now explicitly **Mark human follow-up locally complete**
or **Reopen local human follow-up** from the original issue detail. This
state is private annotation history only: it does **not** close the GitHub
issue, verify that a real human received the escalation, release an active
claim, approve CI, or resolve an infrastructure outage.

- A locally closed human follow-up remains visible in the workbench issue
  list and manual follow-up history, labeled **CLOSED LOCALLY — issue still
  UNVERIFIED**. It no longer contributes to overdue/missing-date reminders
  and is excluded from a newly generated manual calendar (.ics) export.
  Operators may filter explicitly for **Locally closed follow-up**.
- Reopening preserves the original manually entered contact and UTC date
  so that the human can review the case again; it does not notify or assign
  an external person. The underlying GitHub issue stays in normal priority
  triage whether the local human follow-up was completed or not.
- A private export now uses **schema_version 3** and preserves the open
  or local-complete lifecycle value. The exact-board restore and older-board
  carry-forward accept v1, v2 and v3. Historical v1/v2 entries default to
  open. Invalid lifecycle states and mismatched SHA/timestamps are rejected.
- The reviewed carry-forward panel shows when an older handoff is locally
  closed instead of silently reopening it, while preserving the previous
  source hash and reporting any new/changed issue evidence. A local closure
  imported from an older snapshot must still be independently rechecked
  before a human assumes the real issue was handled.

All changes remain private in-memory state until the human chooses a
local export. No background polling, calendar service, source/queue writes,
notification, production mutation, test/QA suite or privilege escalation
was added; the original GitHub, Notion, CI, security and release gates remain.

## 20 October · Unsaved private decisions guard

Private notes, human contact/date assignments, manually closed follow-ups
and reviewed prior-board carry-forward are deliberately held in memory,
never browser localStorage or a remote database. Because closing a tab
could silently discard user work, the operator page now shows whether its
private human annotations have unexported changes. Browsers supporting
the native before-unload confirmation warn before the user leaves a tab
with local changes. The browser controls the actual prompt wording and
may suppress the prompt in some situations; the banner remains the
authoritative visible indication to export.

Use **Download private human handoff** to request a locally saved
source-SHA-bound v3 JSON backup, then verify the file actually arrived.
Edits after that action mark the page dirty again. Selecting an exact-board
handoff to restore prompts for explicit confirmation before replacing
unexported annotations; cancelling preserves all current edits. Importing
the original exact-board snapshot resets the local edited marker only when
validated replacement succeeded. Carrying prior annotations forward marks
the work as unsaved again.

The Markdown briefing or iCalendar export is NOT a full private JSON
backup and does not clear the pending-unsaved warning. No automatic
persistence, cloud save, network requests, worker notifications or
privileged recovery actions are introduced. No QA/regression suites
were added or manually run; all CI, review and security gates remain.

### Private follow-up status is never issue closure

Only an explicit human-only blocker decision (coordinate-owner or park-blocker) can carry a locally completed follow-up. Changing the human disposition to a non-escalation decision resets the local follow-up state to open. Imported v3 handoffs, including older-board carry-forward, reject forged locally closed states attached to unrelated issues. This prevents the local completion marker from impersonating real GitHub issue resolution or a released claim. These are changes to the operator's private data model only: original issue visibility, current live ownership verification and release gates remain unaffected.

### Device-clock review trace, not an external acknowledgement

When a human explicitly closes or reopens a private follow-up, the workbench
records the current device's UTC ISO timestamp as the **last local toggle**.
The timestamp is visible in the issue detail and private Markdown briefing,
and is preserved in v3 JSON exports and reviewed cross-snapshot carry-forward.
Older v3 exports without this optional field and v1/v2 backups remain
importable. Malformed clock values from private files are rejected.

A device-generated time may be inaccurate or forged and is NEVER treated as
evidence that a worker resumed, a GitHub issue was fixed, an owner acknowledged
the request, CI passed or a release occurred. No event is transmitted to
Notion, GitHub, a calendar or any external service; the original lease,
authorization, independent review and deployment gates still apply.

## 20 October · Reviewed bulk human follow-up actions

The offline Product Delivery Workbench now supports a bounded **bulk human
follow-up panel**, built on the existing SHA-pinned source cards and *private
operator annotations*. It operates only on issues explicitly marked by an
operator as `coordinate-owner` or `park-blocker`. All currently open manual
follow-ups may be selected with one click; the user may also select individual
entries. Maximum scope is the current captured board (60 issues), never a
live GitHub/Notion owner inventory.

The operator chooses exactly one local transformation for the selected
entries: mark the private human follow-up **locally completed**, **reopen** a
previously completed human follow-up, or change a specified **UTC review
date** for still-open human follow-ups. The date is validated as a real
calendar day in 2020–2099; completed entries are not silently reopened by
date edits. The selected source issue IDs, current private annotation values,
requested operation and source SHA/observation timestamp are bound to
a reviewed **Preview exact private changes**. A second deliberate confirmation
applies only the rows shown. If selection, local notes, dates or source state
change before confirmation, the preview is refused and must be recreated.

Applying a locally completed/reopened state records one device-derived UTC
toggle timestamp; batch date edits do not forge an event timestamp.
Private contact labels, notes and original source evidence are preserved.
All edits mark the private workbench as unsaved until the user explicitly
downloads a JSON handoff. The follow-up and issue lists refresh immediately,
and local completions cease appearing in future optional .ics calendar
exports. Original GitHub issues remain visible and their live state, owner,
lease, authorization, CI, review, runner and release admission are all
**UNVERIFIED**. No automatic claim, reviewer assignment, CI retry, notifications,
calendar writes, workflow dispatch, VPS command or production deployment
occurs. The existing protected-main and independent review gates are
unchanged. No QA or regression suites were authored or manually executed.

### Source checks directly in the bulk review

Every selectable human follow-up includes a canonical original GitHub-issue link. The private pre-apply review prints that issue URL and the first source-observed missing gate alongside the exact proposed local change, so a human can reopen the original owner/CI/release evidence before confirming. The first displayed gap is not an exhaustive authorization checklist and the original source snapshot may be stale. The operator must still verify all current gates independently; neither preview nor confirmation writes to GitHub, Notion, workers, CI or production.

## 20 October · Calendar contact privacy by default

Calendar follow-up export now **excludes human contact labels by default**.
The original issue link, scheduled all-day review, source-observation timestamp
and an explicit "not an authorization" warning remain so the event is still
usable without shipping a private contact name to any calendar provider.
The optional **Deliberately include private human contact labels** checkbox
is initially unchecked and must be chosen by a person before generating
the private .ics file. Toggling it invalidates the previous calendar preview
and disables its download until the operator reviews exactly the new bytes.

This matters because an intentionally imported .ics file can be synced by
the target calendar client even though zSentinel itself never uses any network
or calendar connector. A contact label is user-entered and **not** a validated
GitHub/Notion assignee or escalation acknowledgement. The selected personal
note and any raw GitHub/Notion data are never included automatically.
No background notification, browser storage, worker dispatch, queue mutation,
privilege expansion or production release was introduced. Source syntax was
directly inspected; no QA/regression suite was authored or manually run.

## 20 October · Conflict-safe local bulk undo

After a reviewed, explicitly confirmed bulk follow-up update, operators can
click **Undo last confirmed local bulk action**. The workbench holds only
the immediately preceding in-memory snapshots for the affected issue IDs,
plus their post-apply values and the unchanged original board SHA.

Undo checks *every* affected issue is still on the same source board and
that its current private annotation still exactly matches the recorded
post-apply value. If even one affected value changed, the **entire undo is
refused** without overwriting anyone's intervening edit. A successfully
validated undo restores all affected private records as one local operation,
disables the one-shot undo button and flags the workbench as unsaved so the
operator can explicitly download a new private JSON handoff. Loading a
different exact-board handoff or importing prior-board annotations clears
the pending undo record. It does not persist in browser storage or a service.

This is not rollback of any GitHub issue/PR, real worker claim, queue,
calendar import, CI, review, deployment or external notification: no such
action was performed. It only reverses changes to the **private human
annotation state** within the current browser session. Original verified
owner/security/CI/release gates remain independent and unchanged.
No QA or regression suites were authored or manually run.

## 20 October · Reviewed offline spreadsheet triage export

The private workbench now offers **Preview filtered offline CSV** followed by
a separate **Download exactly this reviewed CSV** action. The export covers
only currently filtered issue cards (maximum 60) from the already saved,
validated board; it does not query or modify GitHub, Notion, workers, claims,
SQLite, CI, runners or production. The export contains the board SHA-256,
UTC source observation timestamp, canonical issue link, captured issue title
and priority/project, observed candidate PR links, evidence gaps, proposed next
step, manual disposition, private follow-up state and date, plus explicit
`claim_authorized=false`, `merge_authorized=false`,
`deploy_authorized=false` for **each row**. The CSV also preserves the
snapshot's **per-project coverage** (`partial`, `degraded` or `unavailable`),
`inbox_truncated`, `cards_truncated` and `github_denial` flags in each
row. Ordinary issues with no explicitly marked human-only follow-up export
`private_followup_status=not-configured`, not a misleading `open`.
Canonical candidate PR links must match the original issue repository
and cannot be duplicates; malformed or cross-repository candidates cause
the entire untrusted source board to be refused. It is a portable working view
for spreadsheet sorting, **not** proof of current issue state or authority.

By default no private contact labels or free-text operator notes are exported.
The operator can explicitly enable those two extra columns; toggling the
checkbox invalidates the prior CSV preview and requires reviewing it again.
Editing a note, selecting a different issue, changing any filter or changing
the source-derived view also invalidates an existing preview. At download
the complete CSV contents must equal the reviewed bytes, or the action is
refused and preview must be recreated. Exports happen through a local browser
Blob with UTF-8 BOM for spreadsheet import; no automatic network/upload or
localStorage persistence.

Every cell is quoted; control bytes are stripped and leading spreadsheet
formula markers (`=`, `+`, `-`, `@`) are neutralized with an apostrophe
before CSV quoting, including untrusted GitHub issue titles and notes.
The private CSV may still be copied to a cloud provider by the chosen
spreadsheet application: the workbench explains this and the private-note
checkbox defaults off. Original ownership, exact-head checks, independent
review, security/consent and release gates remain unchanged. No QA or
regression suite was authored or manually run.

## 20 October · Provenance-bound two-snapshot CSV change ledger

When an operator loads an earlier, validated private board, the offline
comparison now also enables **Preview full change CSV** and a separate
**Download reviewed two-snapshot CSV** action. This is a product export,
not a new live GitHub/Notion observation. It serializes **all bounded issues
from the two saved boards** (up to 60 in each), distinguishing newly
observed, changed evidence, still observed and no-longer-observed items.

Each row records both exact source SHA-256 hashes and UTC observation
times, issue identity/link, before/after priorities and projects,
the categories of changed source fields, both project-coverage labels,
and both snapshot truncation/denial flags. The stable field
`not_observed_proves_resolved=false` explicitly prevents interpreting
an absent row as fixed, merged, released or remotely closed. All three
authorization columns remain `false`. There are no private free-text
annotations, owner labels, current claim assertions, network calls,
issue mutations or external syncs in this change ledger.

The full CSV must be reviewed before saving. If the earlier board is
cleared or replaced or the underlying comparison differs, the previous
preview is invalidated; download recomputes and byte-compares the
provenance-bound CSV. All fields use the same quoted, control-normalized,
formula-safe CSV handling as the single-board export. A UTF-8 BOM aids
manual spreadsheet import. No QA suite was authored or manually executed;
normal exact-head review, CI, protected-main and runtime gates remain intact.


## 20 October · Merge-first PR desk and unlinked product issue queue

The already saved bounded delivery-board now powers a separate **Merge first**
section in the same offline HTML workbench. For the operator's currently
selected project, severity, disposition, follow-up and historical-change filters,
it groups captured candidate PR links **by canonical URL**, shows each PR
only once, orders groups by highest linked issue severity (P0 before P1),
and displays original issue titles, issue links, local human disposition and
observed missing-evidence text. Multiple issue references to one PR are
shown explicitly as coordination candidates, **not** proof of duplicated
implementation, conflict, closure or current PR ownership.

The companion **product issues with no recorded PR reference** section
makes free-looking backlog visible without falsely implying availability:
the source is intentionally bounded, and a missing saved candidate does not
establish that live GitHub has no open PR or that Notion has no active claim.
No new worker task or branch is automatically created.

**Preview filtered merge-first handoff** produces a source-SHA256-bound
Markdown snapshot of both groups, including captured UTC time, canonical
public issue/PR URLs, original priority, missing evidence and the fact that
current exact-head CI, reviews, branch rules, worker claim and release
permissions are unknown. **Download exactly this reviewed PR handoff** accepts
only the unchanged complete preview; adjusting filters or private human
dispositions invalidates the preview. The output is a local browser download.
Private personal notes and contact labels are *not* included. No GitHub,
Notion, browser persistence, queue, runner or production mutation occurs.

The feature reuses existing canonical board validation, URL admissions,
bounded card/reference lists, DOM text nodes and strict offline CSP. It
does not infer `merged=true` from a historical PR reference or enable any
automatic claim, merge, review, dispatch or deploy. Existing mandatory CI
and independent review gates remain separate. No new QA, regression or
test suites were written or run.


### Structured review-only JSON manifest

The same Merge-first Desk also supports **Preview exact review manifest (.json)**,
then a separate deliberate **Download exactly this review-only JSON** action.
The manifest includes a versioned schema, source board SHA256 and UTC
observation, current filter count, bounded coverage flags and de-duplicated
candidate PR-to-issue relationships, plus unlinked issue references. It
includes the original public GitHub URLs, captured product priorities and
missing evidence, without the local operator's private notes or contact
labels. Each PR and each unlinked issue has `active_claim_verified=false`;
the root explicitly records `claim_authorized=false`,
`merge_authorized=false`, `deploy_authorized=false` and
`dispatch_authorized=false`.

The preview is rendered as plain text. Download re-derives the entire manifest
from the current filtered source and refuses byte drift. All original link
identities were already validated when opening the source board, and
the manifest is not a current GitHub state query or machine-executable
dispatch/release instruction. Only an independently authorized operator or
worker may later inspect a candidate using live ownership, CI and release
evidence. No automatic workflow, background scheduler, claim or PR mutation
was installed.


## 20 October · Saved live GitHub gate source overlay for merge-first triage

The workbench now optionally imports a **single saved, bounded JSON GitHub
PR gate observation** and attaches it to *only* canonical PR URLs already
linked from the validated, offline delivery board. This bridges the local
Merge-first operator view with the source created by the separate zSentinel
live issue/PR scope cockpit in [PR #1567](https://github.com/Zennay/zCloud/pull/1567).
It does not merge either pending PR and does not make the offline tool
into a GitHub client.

### Workflow (once both source PRs have passed required integration gates)

Produce a saved read-only report using the authorized operator's own local
GitHub credentials and the **existing** command in the PR #1567 cockpit:

```sh
python3 scripts/zsentinel_issue_scope_cockpit.py \
  --live-issues --project cloud --inspect-pr-gates \
  --max-prs 10 --format json > saved-github-pr-observation.json
```

The output can be imported into the already-open local Product Delivery
Workbench through **Import GitHub PR-gate snapshot**; it requires no browser
network access, token, service, Notion claim or background scheduler. A direct
saved `zsentinel-current-pr-gate-observation-v1` JSON receipt is also
accepted. The file must be under 1 MiB, use a supported versioned schema,
contain the source's explicit non-authorizing flags, have a valid nonfuture
UTC observation timestamp and match at least one original board PR link.
Malformed, foreign, conflicting, changed-head or unavailable evidence never
promotes a PR to merge-ready. One failed import keeps the previous overlay
unchanged. The overlay is memory-only and can be cleared explicitly.

The optional **PR gate focus** narrows *only the grouped PR references* to
observed failing CI, pending CI, review changes/missing approvals, missing or
incomplete receipt, or apparently completed observed checks. An observed
green group still says **NOT MERGE AUTHORITY**: the real current PR HEAD,
required branch rules, independent review and owner/release consent are
not established by this saved JSON. Within priority order, failed and
pending observations are shown first. The independent list of product
issues without captured PR links remains visible and never becomes a
claimable queue.

Imported observations show the saved HEAD prefix, modern and legacy
pending/failed check counts, independent review count, source completeness,
explicit human next gate and age. Receipts older than 15 minutes are labeled
**STALE**; newer receipts remain *observational*, not live authority. The
exact imported source bytes are SHA-256 pinned. The reviewed Markdown and
JSON handoffs include that hash, their observed source time and explicitly
false authorization flags, never raw credentials, private contacts or notes.
All downloads require a preview of the *current* filters/imported source,
and modifying the overlay invalidates existing previews.

The feature writes no issue, PR, Notion page, queue, worker lease, runner,
workflow, VPS service or deployment. It performs no merge/restart/dispatch
and adds or manually invokes no QA/regression suite. Current protected
main, mandatory CI, review and release rules apply independently.


## 20 October · Complete offline → current GitHub → offline release triage

The offline operator can now export a source-bound
`zsentinel-offline-merge-first-review-v1` JSON handoff and re-inspect its
**highest-priority existing PR references without manual retyping** via the
read-only collector in the already-open
[PR #1567](https://github.com/Zennay/zCloud/pull/1567).

1. In the workbench, set desired project/P0/P1 filters and deliberately
   download **Download exactly this review-only JSON** after preview.
2. In an *authorized local* environment with configured GitHub access,
   run the read-only existing-PR gate command:

```sh
python3 scripts/zsentinel_pr_gate_now.py \
  --from-review ./zsentinel-offline-merge-first-review.json \
  --output ./private-current-head-gates.json
```

3. Back in the original offline workbench, import the new JSON using
   **Import GitHub PR-gate snapshot**, then inspect the red/pending/review
   groups and optionally download a reviewed Markdown or JSON handoff.
   If the source handoff contains more than four referenced PRs, run a
   separate bounded `--batch 2` command with another new output filename,
   then import **that** observation. The interface displays the selected
   batch number and total; one imported file does not cover other batches.
   A user must separately confirm current PR HEAD, independent human review,
   protected branch/check policy, active claims and deployment authority.

The importer compares the new source receipt's **original board SHA256**
against the actual currently opened offline board, validates optional
batch index/total/start/count against the full source candidate count,
and rejects unrelated boards and malformed/partial provenance without
discarding an existing accepted overlay. The UI explicitly shows `source_review_pr_count` and
warns when a >4-candidate source was **only observed for the first four
highest-priority PR references**. The exact imported gate receipt bytes are
also SHA256 pinned. Older than 15-minute sources are marked stale.

No previously saved observations can confirm live mergeable status or
current required rules. Neither this local workbench nor the bounded
GitHub collector claims issues, reruns CI, merges PRs, dispatches workers,
writes to VPS or deploys to production. Both source PRs must independently
clear mandatory CI, protected-branch and review conditions before this
workflow exists on `main`. No QA suites were created or run.


## 20 October · Complete saved gate-batch bridge to the existing offline browser

The new **`scripts/zsentinel_saved_gate_batch_bridge.py`** joins saved
current-head PR-gate receipts from the existing read-only collector in
[zCloud PR #1567](https://github.com/Zennay/zCloud/pull/1567), into **one**
`zsentinel-integrated-issue-pr-scope-v1` JSON document that the existing
**Import GitHub PR-gate snapshot** browser input in this workbench already
accepts. The offline browser importer now also validates the bridge's pinned
original board SHA256, complete-batch marker, bounded oldest/newest UTC
timestamps and exact set membership of captured PR URLs. It visibly labels
the receipt set as complete *saved batch coverage*, never complete
mandatory CI/review or merge authorization. Network restrictions, runtime
and owner claims remain unchanged.

Use a **previously reviewed and downloaded** exact JSON manifest and
the separate non-mutating collector's output for *every* numbered batch.
The bridge deliberately uses the **oldest** batch observation as the browser's
overall timestamp so it does not present stale members as fresh.
Example for a nine-PR manifest, after separately collecting the three
read-only batches using `zsentinel_pr_gate_now.py --from-review ... --batch 1/2/3
--output ...`:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./reviewed-manifest.json \
  --receipt ./private-gates-batch-1.json \
  --receipt ./private-gates-batch-2.json \
  --receipt ./private-gates-batch-3.json \
  --output ./new-private-combined-gates.json
```

Then import **new-private-combined-gates.json** in the offline workbench.
The result contains only original canonical issue-to-PR associations,
the bounded allowlisted exact-head observation fields, original manifest
SHA256, original board SHA256, observed batch coverage and explicitly
false claim/merge/deploy/writes flags. It is created exclusively as a new
private `0600` file with an `fsync` before the command succeeds.
No existing file is overwritten. Neither original receipts nor any
privileged GitHub/Notion/VPS state is modified.

**Fail-closed rather than silently showing a partial backlog:** all
`ceil(PR count / 4)` exact numbered receipts must be supplied. The bridge
verifies their raw manifest SHA, original saved board identity, exact
priority/repository/PR sort order and batch boundaries against the
existing collector's contract; it refuses duplicate/missing batches,
unexpected PRs, changed/mixed source identity, source data outside a
ten-minute observation window, foreign issue relationships, and
unbounded files. Limits: 300 PRs, 75 batches, 60 issue rows, up to five
PR references per issue, at most 1 MiB per input/output and 8 MiB of
combined input. When any bound is exceeded, no combined import is created;
the operator can use smaller separately reviewed manifests.

This is **saved source evidence**, not fresh or necessarily complete
mandatory checks, branch rules, human reviews, current worker ownership,
or deploy authority. Even a complete file set may contain unreadable PR
observations or checks still queued. Operators must open each real PR
and independently verify its current exact head, required branch
protection, independent review and authorization. No QA/regression
suite, CI rerun, paid runner, live worker or production action is
initiated by this bridge.


### 20 October · Trustworthy saved PR triage and strict input admission

The original offline Merge-first Desk now displays one **saved PR gate triage
overview** for its current human-selected filter: failed observations, pending
checks, review required, unavailable/incomplete source and the explicitly
non-authorizing category of apparently clear sampled checks. It always labels
these as saved observations. The private Markdown handoff includes the same
overview. A structured `saved_gate_triage` section in the reviewed JSON
manifest makes the observed categories available to a subsequent *human*
operator without turning them into a worker assignment or merge command.
All live exact-head required checks, current claim, branch rules, independent
reviews and owner/release permissions remain `false`/unverified.

The companion `zsentinel_saved_gate_batch_bridge.py` additionally refuses
cross-repository or duplicate issue-to-PR links in saved manifests, even when
their individual URLs are syntactically valid. Untrusted JSON is now admitted
through a single bounded no-follow/nonblocking **regular-file descriptor**;
the file identity and size are compared before/after the read. Duplicate
JSON keys (including a second `merge_authorized` field), nonfinite numbers,
replaced/symlinked paths, nonregular files and visibly changing bytes are
rejected before any import is written. The source limit, UTC bounds,
all-batches-required rule and private output permissions remain unchanged.

These are product changes on the **existing open PR #1560**, not a
standalone QA initiative. Source-acceptance and optional normal mandatory
CI remain separate from authorization to merge or deploy.


### 20 October · Original board membership survives combined import

After importing a complete saved-gate bridge, the browser now checks
**every reported issue ID and issue URL** against the exact already-open
validated delivery board. Each reported PR must appear in that original
issue card's captured PR links. Repeated issue entries, repeated PR links
for one issue, foreign board issues, and missing gate observations are
refused before the existing accepted overlay is replaced.

This preserves the intended meaning of the complete historical batch
indicator. It says *all saved receipt batches for one source-bound
review manifest were examined*, never that all portfolio PRs were
examined or that exact-head required CI, branch protection, independent
review, production safety, current claims or release permissions
passed. Saved gate categories are also included in the reviewed
Markdown/JSON operator handoff, with false authorization flags.

Latest implementation on the same existing PR; no owner or active-claim
transfer, source-only change, no QA suites written or manually executed.


### 20 October · Continue with safely available batches

The offline saved-gate bridge now offers an **explicit partial import** when
one source batch is absent or unavailable. This avoids freezing all unrelated
PR observations while preserving source integrity and mandatory merge gates.

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./reviewed-manifest.json \
  --receipt ./private-gates-batch-1.json \
  --receipt ./private-gates-batch-3.json \
  --allow-partial \
  --output ./new-private-PARTIAL-gates.json
```

Without `--allow-partial` the original all-batches-required behavior remains
the default. Each provided receipt still passes exact original manifest SHA,
original board SHA, ordered four-PR batch membership, strict UTC source time
window, allowlisted observational fields, and identity-bound, regular-file,
size/duplicate-JSON checks. Missing source cannot become a success.

The combined JSON carries `observed_batch_count`,
`expected_batch_count`, `review_candidate_pr_count`,
`missing_batch_count`, sorted `missing_batch_indexes`, and
`all_batches_observed_not_all_checks_required_verified`. The browser
verifies these fields together: no forged complete flag, missing index outside
the manifest, duplicate index, or contradictory totals is accepted.
It reports **PARTIAL saved batch coverage** and exact missing batch indexes.
For the versioned bridge, it additionally recomputes the number of distinct
PR receipt identities expected from the manifest's candidate count and
received batch indexes, and refuses reports that silently omit one PR from
a supposedly supplied batch. The human review-only JSON export separately
records the original candidate count, distinct observed PRs, and missing
batch indexes. A fully supplied source still does **not** establish green
required checks, independent review or merge authorization.
Only actually supplied PR observations appear; other captured PRs remain
**UNKNOWN**. The existing original board membership/URL checks continue
to apply to every imported issue/PR association.

The workbench's read-only human Markdown and JSON export carries source
coverage and missing indexes. A *complete saved batch set* is still not
evidence that required current GitHub workflows succeeded, the current PR
HEAD remained unchanged, independent owner reviews occurred, a worker slot
is free, or deployment was authorized. It never triggers a dispatch,
retry, merge, claim, external network call or file overwrite. Refuse reports
with zero receipts; unverified input or unsupported schema cannot be
partially ingested.

No QA/regression suite, runner restart or privileged production task is
needed for this offline source feature.


### 20 October · Direct PR navigation for missing batches

The partial bridge now records `missing_batch_prs`: for every absent
original four-PR review batch, it includes its **exact saved canonical PR
links**. These are extracted from the original previously reviewed manifest;
they are NOT live-open, current-head, check, review, ownership or deployment
evidence.

The browser validates the missing batch index sequence, exact expected PRs
per numbered batch, uniqueness and that every URL belonged to the already
validated original saved board. A missing-batch PR cannot simultaneously
appear as a received gate receipt. Invalid source fails atomically without
replacing a previously imported observation.

In the merge-first desk, a new **PR links with no saved gate observation**
area provides the original clickable links, grouped by missing batch,
explicitly marked **UNKNOWN**. The operator can use the existing
`zsentinel_pr_gate_now.py --from-review <original-reviewed-manifest>
--batch <number>` read-only collector for just the missing batch when
appropriate. The Markdown and JSON review-only handoffs include the
same unobserved PR links; neither output constitutes permission to inspect
private data, claim work, start CI, merge or deploy.

All original source limits, exact-head release gates, private local file
permissions and no-network browser restrictions remain unchanged.


### 20 October · Native offline multi-batch PR gate import in browser

The existing merge-first workbench can now combine original reviewed
manifests and one or more saved gate batch receipts *without any
command-line step*. In the **Import GitHub PR-gate snapshot** panel,
expand **Combine original reviewed manifest and saved PR batches here
(offline)**, select the exact previously downloaded
`zsentinel-offline-merge-first-review.json` and select the corresponding
JSON output files from the read-only
`zsentinel_pr_gate_now.py --from-review ... --batch N` commands. Press
**Combine saved batches in this browser**. This processes only
explicitly selected local files using browser File/crypto APIs; the
offline page does not contact GitHub, Notion or the VPS and cannot
start, retry, merge, claim or deploy work.

The browser rejects a mismatched board SHA, manifest SHA, foreign or
duplicate original PR/issue associations, improperly ordered batches,
repeated batch indexes, non-canonical PR URLs, unbounded inputs and
contradictory non-authorizing gate flags. It uses the same numeric PR
and case-sensitive ASCII repository sort as the Python source
converter. Input is restricted to **one <=1 MiB manifest, 1–75
<=1 MiB receipts and 8 MiB total**. All received batches must fall
within a common 10-minute saved observation window; the timestamp
used for source age is the **oldest** batch. Invalid input leaves the
previously imported overlay untouched.

Selecting a subset of originally available batches is supported.
The UI explicitly shows **PARTIAL** coverage, exact missing indexes,
the original corresponding PR links as **UNKNOWN**, and observed
gate categories in the existing merge-first desk and private
Markdown/JSON human handoffs. Supplying all original batches yields
**COMPLETE saved-source coverage**, never complete required checks,
independent approval, live ownership or release authority. Users may
still import one gate JSON or a precombined CLI bridge using the
existing single-file control; neither path loses its behavior.

The local source inspection for this feature checks Python
syntactic validity and embedded browser JavaScript syntax only.
No QA/regression suite, CI retrigger or privileged VPS task was added.


### 20 October · Focused missing-batch collection handoff

When saved historical batches are incomplete, the workbench now also
prints the *manual read-only* command for each missing batch beside the
unobserved PR links. The same command is included in the private Markdown
operator handoff. For example, with batch 2 missing:

```sh
python3 scripts/zsentinel_pr_gate_now.py --from-review ./zsentinel-offline-merge-first-review.json --batch 2 --output ./zsentinel-saved-gate-batch-2.json
```

The command is rendered as text, **never executed**, and its batch index
is bounded to 1–75. The operator decides whether/when to collect new
GitHub observations; it requires access to the exact original reviewed
manifest and must respect current GitHub/API and project ownership gates.
New data can be imported via the same in-browser multi-batch selection.
No CI retrigger, write to GitHub, claim, merge or deploy is performed.


### 20 October · Save and later restore an in-browser combined source

The browser's **Combine original reviewed manifest and saved PR batches**
path now also produces a *portable offline saved-source snapshot*.
After successful source-bound import, select **Preview portable, private
combined JSON**, inspect the exact source (and its non-authorizing
provenance flags), then **Download exactly this reviewed combined JSON**.
Nothing is downloaded automatically or transmitted to GitHub, Notion,
the VPS or another service. The output filename is
`zsentinel-offline-source-bound-pr-gates.json`.

On another offline session, open the ordinary **Saved PR gate JSON**
single-file importer and select that saved combined file. It contains
the same `zsentinel-integrated-issue-pr-scope-v1` schema as the Python
batch bridge, including source board and manifest hashes, oldest/newest
saved source timestamps, all/partial coverage and exact missing
batch indexes/PR links. All check/review rows are restricted to the
canonical allowlist and explicitly disallow claims, merges, deployments
and writes. File size is capped at 1 MiB, and the generated combined
JSON is checked against the workbench's existing single-file importer
*before* it can replace the current overlay.

Only actual received batches are included. PRs in missing batches remain
UNKNOWN; saving and restoring a JSON does not turn historical observations
into live required-check or independent-review evidence. If the reviewed
download preview or imported source changes, preview is required again.
The browser never persists personal notes, private gate observations or
files silently. Python AST and embedded JS syntax were checked on the
exact pinned branch source (not a QA or regression suite).


### 20 October · Compare saved PR gate changes, without inventing live recovery

The existing PR gate panel now includes **Compare an older saved PR-gate
snapshot with this imported source (offline)**. First import the newer
single-file or browser-combined gate observation. Then select an
**older saved** gate JSON from the same original captured board. The
older UTC source timestamp must precede the newer observation. The
normal source-validation path checks the older JSON before comparison.

The view lists PR references whose **saved observational state**,
same-HEAD pending/failing/approval counts, or sampled HEAD changed.
Each row links to its original PR. A changed HEAD is always marked
**NOT COMPARABLE** for old versus new check counts. Unavailable, absent,
incomplete and no-check-context sources are **UNKNOWN**, not a passed
workflow or PR. If the samples do not differ, the view explicitly
says this is *not* evidence that mandatory checks are green today.
Both exact saved UTC observation times appear in the report.

The operator can preview a Markdown **historical PR gate difference**
report and deliberately download those exact reviewed bytes. The
report contains the original source-board identity and both selected
JSON SHA-256 digests, with prominent labels that active claims, live
GitHub HEAD, required branch rules, independent approval and release
authority were never verified. Replacing or clearing the current gate
source clears any previously selected comparison and its export.

No GitHub request, runner operation, claim, CI rerun, merge, deploy
or local automatic persistence is made by the browser. This provides
human incident recovery context only; the historical comparison
cannot establish live delivery or authorization.


## Privé merge-first PR-gate-overdracht op basis van opgeslagen receipts (20 oktober)

De bestaande `zsentinel_saved_gate_batch_bridge.py` kan de al verzamelde,
aan één reviewmanifest gebonden batches nu zonder browser, GitHub API,
Notion-schrijfactie of nieuwe gate-run samenvatten in **direct leesbare Markdown**:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review.json \
  --receipt ./gate-batch-1.json --receipt ./gate-batch-2.json \
  --format markdown --output ./private-merge-first-handoff.md
```

Laat `--format` weg voor de bestaande `json` browserimport. Het Markdown
volgt **de oorspronkelijke P0/P1-prioriteit en bestaande PR's**, noemt per
PR de gekoppelde bronissues en — alleen als een geldig exact-head-receipt
bestaat — de geobserveerde SHA, steekproef van CI-statussen, onafhankelijke
reviews en menselijke vervolgstap. Ontbrekende batches zijn zichtbaar
**NIET BEOORDEELD**, onbereikbare GitHub-bronnen **ONBEKEND**, en afgekapt
geretourneerde check-/reviewpagina's **GEDEELTELIJKE BRON**.

Beide formaten zijn opnieuw uit **dezelfde streng begrensde, SHA-gebonden
sourcebewijzen** opgebouwd; onbereikbare/HEAD-gewijzigde PRs dragen geen
onbetrouwbare gate-details over naar het browserimportbestand. De output
wordt exclusief als nieuw privébestand `0600` aangemaakt en overschrijft
geen bestaande bestanden. Zonder `--allow-partial` wordt iedere ontbrekende
batch geweigerd. Met de expliciete optie blijft de ontbrekende bron
onbeslist, nooit succesvol. Het rapport sluit **merge-/deployautorisatie,
actieve claimbeschikbaarheid en main-oplevering uit**: verse exact-head-CI,
branchregels, echte reviewergoedkeuring en serialized release zijn apart
vereist. Geen QA-suite of eigen workflow wordt geschreven of gestart.


### Snelle vrijgaveblokkade-inbox in de privé-overdracht

Bij een grote reviewmanifest met maximaal 300 eerder gevonden PRs bevat
`--format markdown` vóór de individuele PR-kaarten een **één-scherm
blokkeerinbox**. De bestaande kandidaten staan daarin volgens de strikte
bronwaarheid onder ontbrekende batch/GitHub-bron, waargenomen niet-geslaagde
checks, lopende checks, gevraagde reviewwijzigingen, gedeeltelijke CI-bron,
nog niet waargenomen onafhankelijke review of onbekende verplichte
release-/eigenaarsregels. Per categorie toont hij maximaal zes rechtstreeks
klikbare, canonieke PR-links en het resterende aantal; alle PR-kaarten en
bronissues blijven daaronder aanwezig, P0 voor P1. Zo hoeft de operator niet
honderden kaarten af te lopen voordat de reden voor stilstand zichtbaar is.

Ook de **leeftijd van de oudste opgeslagen receipt** staat nadrukkelijk in
de samenvatting; na zes uur wordt die als verouderd gemarkeerd. Dit is alleen
een weergave van één bevroren, read-only bewijsset. Nul fouten in een sample
geeft nooit groen licht: exacte verplichte GitHub-checks, benodigde reviews,
actieve claims, hoofdbranchmerge en productie zijn afzonderlijk te verifiëren.


## Eerdere PR-gatewaarneming vergelijken (20 oktober · merge-first)

Dezelfde bestaande CLI kan één vorige, zelf gegenereerde **private JSON
gate-bridge** vergelijken met nieuwe bronreceipts van exact hetzelfde
reviewmanifest. Een oude bestandsmomentopname opnieuw gebruiken is geen
nieuwe CI-check en er worden geen externe GETs of workflowruns gestart.

```sh
# Eerste momentopname: bronreview plus oorspronkelijke 1–N receipt-batches.
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./old-gates-1.json --receipt ./old-gates-2.json \
  --output ./prior-gate-bridge.json

# Daarna zelf de werkelijk nieuw verzamelde, tijdsvolgende gate-receipts.
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./new-gates-1.json --receipt ./new-gates-2.json \
  --previous ./prior-gate-bridge.json \
  --format markdown --output ./private-gate-delta.md
```

De nieuwe briefing begint met **wat veranderde sinds de vorige privéwaarneming**
en behoudt alle oorspronkelijke prioritaire P0/P1-PR-kaarten. Voor iedere
gekoppelde bestaande PR wordt alleen onderscheid gemaakt tussen:
nieuwe gate-steekproef, uit huidige steekproef gevallen bron, gewijzigde
waargenomen exacte HEAD, veranderde CI-/reviewsteekproef of onbereikbare
bron. Verdwijning uit een begrensde steekproef is expliciet **geen bewijs
van een merge, fix of vrijgave**.

De vorige JSON moet afkomstig zijn van **exact hetzelfde SHA256-gebonden
reviewmanifest en board**, met exact gelijk aantal batches en ouder
afgesloten observatievenster. Een ander project-/issuevenster,
mismatchende bron, vreemd PR-adres, latere/tijd-overlappende waarneming,
autorisatieclaim of corrupte/private ongeldige bron wordt geweigerd.
De oude file wordt slechts één keer veilig geopend als bounded regular file,
zonder symlink, en nooit aangepast; de uitvoer blijft create-only 0600.
Met `--format json` is `--previous` bewust niet toegestaan om het bestaande
browser-importcontract onveranderd te laten.

Zelfs wanneer er **geen waargenomen verschil** is, blijven verplichte
exact-head GitHub CI, onafhankelijke review, eigenaarschap, protected main,
releasewriter en live deploy afzonderlijk te controleren. Alleen een echte
`merged=true` respons en exacte nieuwe default-branch SHA tellen als
source-integratie op main. Geen QA-code, automatische claim, dispatch,
workflow of productieactie is onderdeel van deze uitbreiding.


## 20 oktober · Eén commando naar een zelfstandige offline HTML PR-afrondingswerkbak

De al bestaande opgeslagen exact-head PR-gatebatches kunnen nu rechtstreeks
tot een **volledig offline, zelfstandige, mobiel leesbare HTML-werkbak**
worden verwerkt. Dit is een extra productuitvoer naast de bestaande
JSON-browserimport en de volledige Markdown-overdracht: er is dus geen
separate workbench-import, webserver, browserextensie of live GitHub-token
nodig voor het bekijken van reeds opgeslagen bewijs.

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./gate-batch-1.json \
  --receipt ./gate-batch-2.json \
  --format html \
  --output ./private-merge-first-workbench.html
```

Alleen reeds verkregen **identiteitsgebonden, begrensde receipts** worden
gebruikt. Zoals bij `--format json` en `--format markdown` weigert
de standaardroute ontbrekende batches; met expliciet
`--allow-partial` krijgen niet-geziene batches **BRON ONBEKEND**.
Een eerdere momentopname `--previous` blijft bewust **alleen**
ondersteund bij `--format markdown`, zodat huidige HTML-kaarten nooit
historische CI als verse gate presenteren.

Het nieuwe werkblad bevat:

- Een aanklikbaar overzicht van de **waargenomen blokkaderedenen**,
  met bronuitval, falende checks, pending checks, gevraagde
  aanpassingen, onvolledige gate-dekking, ontbrekende onafhankelijke
  review en onbekende verplichte branch-/ownerregels strikt gescheiden.
- Per kandidaat-PR een oorspronkelijke canonieke GitHub-link, P0/P1/P2
  issueprioriteit, gekoppelde bronissues, eventuele **waargenomen**
  HEAD en begrensde CI-/reviewtellingen plus de menselijke vervolgactie.
  PR's worden maar eenmaal getoond, ook wanneer meerdere issues
  dezelfde bestaande PR noemen.
- Per categorie open-/dichtklapbare kaarten met P0-eerst bronvolgorde,
  bruikbaar op mobiel en op papier, inclusief oudste/nieuwste
  waarnemingsmoment, het aantal onbekende batches, beide SHA256
  brondigests en een duidelijke zesuur-stale-waarschuwing.

**Privé en geen schijnvrijgave:** de HTML heeft geen JavaScript, API,
webformulier, opslag, workflowknop of externe bronnen. De CSP blokkeert
scripts, netwerkverbindingen en externe afbeeldingen. Canonieke
GitHub-links openen uitsluitend na een bewuste klik in de browser.
De bron komt uitsluitend uit de al bestaande strict-bounded
`combined_manifest`-admission en een aanvullende fail-closed HTML
weergavecontrole. De bestandsuitvoer blijft create-only, 0600,
`O_EXCL` en maximaal 1 MiB. Een bron-HEAD die er groen uitziet,
geeft **NOOIT** bewijs voor de actuele verplichte CI, reviews,
branch protection, eigenaarschap, een merge of live productie.
Er is geen QA- of regressiesuite toegevoegd of uitgevoerd en
geen VPS-/GitHub-/Notion-mutatie gestart vanuit de pagina.


### Optioneel: twee bronmomenten in dezelfde offline HTML bekijken

De zelfstandige HTML-werkbak kan nu ook een oudere privé-JSON bridge
van **precies hetzelfde originele reviewmanifest** naast de later
verzamelde gate-receipts tonen:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./nieuwe-gate-batch-1.json \
  --receipt ./nieuwe-gate-batch-2.json \
  --previous ./vorige-private-gate-bridge.json \
  --format html --output ./private-merge-eerst-met-wijzigingen.html
```

Boven de huidige brongebonden PR-blokkades verschijnt een apart
open-/dichtklapbaar onderdeel: **welke bestaande PR's veranderden
tussen twee daadwerkelijk opeenvolgende bewaarde bronmomenten?**
Canonieke PR-links zijn rechtstreeks aanklikbaar. De oorspronkelijke
vergelijkingslogica uit `prior_gate_delta` wordt **hergebruikt**:
beide bronnen vereisen hetzelfde board- en reviewmanifest-SHA,
niet-overlappende oplopende UTC-observaties, dezelfde PR-issue-identiteiten,
correcte batchdekking en expliciet niet-autoriseren.

Een gewijzigde HEAD maakt eerdere review-/CI-getallen nooit geldig
voor de nieuwe HEAD. Een verdwenen PR uit een *steekproef* is
geen bewijs dat de PR is gemerged, ingetrokken of afgerond.
De HTML ontsluit alleen het reeds opgeslagen bronsignaal; dezelfde
privacygrenzen, no-script-CSP, create-only 0600 output en vereiste
verse GitHub-/Notion-/releasebevoegdheden blijven intact.


### Projectoverzicht en directe PR-sprongen

Het HTML-product heeft nu ook een **projectnavigatie** zonder JavaScript:
iedere waargenomen repository toont zijn eigen begrensde lijst met
bestaande PR-kandidaten in P0/P1/P2-volgorde. Vanuit die lijst springt
de operator direct naar de betrokken PR-kaart in de al geopende
blokkeringsgroep, zonder eerst door tientallen irrelevante kaarten
te scrollen. De geselecteerde kaart wordt zichtbaar gemarkeerd.
De oorspronkelijke GitHub-PR en bronissues blijven afzonderlijk
klikbaar voor vrijwillige actuele hercontrole.

De index is uitsluitend afgeleid van dezelfde maximaal 300 valide
bronmanifestkandidaten; de broninventaris moet één-op-één overeenkomen
met de getoonde kaarten. Een lege index of een historische PR-link
is geen eigendoms- of mergebewijs. Er wordt geen nieuwe API-bron
bevraagd en de HTML blijft offline, scriptvrij en niet-autoriseren.


## 20 oktober · Sorteerbare privé PR-afrondingslijst (CSV)

De al beschikbare, aan één reviewmanifest en exacte bronboard-SHA
gebonden gatebatches kunnen nu naast `json`, `markdown` en `html`
rechtstreeks als **offline semikolon-CSV** worden overgedragen:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./gate-batch-1.json \
  --receipt ./gate-batch-2.json \
  --format csv --output ./private-bestaande-pr-werkbak.csv
```

Hiermee kan de operator in een lokale spreadsheet **per bestaande PR**
sorteren op oorspronkelijk issueprioriteit, project, waargenomen
blokkeringsreden of vervolgactie, zonder een tweede browserimport
of extra GitHub-/Notion-aanroepen. Iedere PR komt maximaal eenmaal
voor, ook bij meerdere gekoppelde issues. Canonieke bronissue- en
PR-links, waargenomen SHA, CI- en reviewtellingen, bronvenster en beide
SHA256-bronidentiteiten blijven in elke rij behouden.

**Betekenis van lege versus nul-waarden:** als een batch ontbreekt
of GitHub-bron onbekend is, zijn CI-/reviewtellingen **leeg** en staat
de batchstatus op niet waargenomen/onbeschikbaar. Een echt waargenomen
nul wordt numeriek weergegeven. De kolommen voor verplichte
CI-/reviewvrijgave, actuele claim, main-merge en live-status blijven
altijd expliciet **ONBEKEND**. Dit is dus nooit een release- of
reviewgoedkeuring.

Het bestand gebruikt UTF-8 met BOM en puntkomma's voor gebruikelijke
Nederlandse spreadsheetinstellingen. Alleen canonieke repository-/PR-
en issue-identiteiten, gecontroleerde SHA/tijdstempels, getallen en
vaste Nederlandse labels/acties worden als cellen toegelaten:
geen onbetrouwbare vrije providertekst of formules. De bestaande
`combined_manifest`-validatie en create-only privé-bestandsuitvoer
(`0600`, `O_EXCL`, maximaal 1 MiB) blijven gelden. `--previous`
blijft uitsluitend voor `markdown` en `html` beschikbaar,
zodat een historisch CSV-importbestand niet als actuele mergevrijgave
kan worden geïnterpreteerd. Er wordt geen QA-suite aangemaakt of
gestart, geen werk overgenomen en geen CI, claim, queue of VPS gewijzigd.


### Meer dan één gelijktijdige PR-blokkade blijft zichtbaar

Een GitHub PR kan in hetzelfde bronmoment **tegelijk** een falende
check, wachtende CI, gevraagde reviewwijzigingen, ontbrekende
onafhankelijke goedkeuring en onvolledige bronpagina's hebben.
De eerder aanwezige ene prioritaire categorie blijft bestaan voor
de hoofdgroepering. De actuele HTML-kaart toont nu daarnaast alle
andere waargenomen blokkades onder **Ook waargenomen**. De CSV-kolom
`Gelijktijdige blokkades` bewaart dezelfde volledige begrensde lijst,
zonder onbetrouwbare providerteksten.

Dit voorkomt dat een operator uitsluitend op CI-wachten stuurt
terwijl een onafhankelijke review- of eigenaaractie al nodig is.
Het is uitsluitend **waargenomen werkopvolging**: één bronsteekproef
bewijst geen volledige verplicht-checklijst, eigenaar, fix,
`merged=true`, productieherstel of vrijgave. Alle bestaande
code-eigenaar-, CI-, security-, release- en Notion-claimgates
blijven ongewijzigd; er is geen automatische actielus,
workerclaim of test/QA-run toegevoegd.


## 20 oktober · Portfolio-brede merge-first voortgang uit bestaande PR-bronnen

De zelfstandige, reeds brongebonden **saved-gate batch bridge** heeft nu
één canonieke **projectwerkbak** boven de individuele PR-kaarten.
Vervolgens blijft de gehele oorspronkelijke bestaande PR-inventaris
beschikbaar, zonder een tweede crawler, extra GitHub-opvraag of
runtime-schrijfbevoegdheid.

```sh
# Self-contained offline HTML: P0-eerst per project met bron-PR-links.
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./gate-batch-1.json \
  --receipt ./gate-batch-2.json \
  --format html --output ./private-portfoliobord.html

# Semikolon-CSV met één menselijke vervolgactie per waargenomen project.
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./gate-batch-1.json \
  --receipt ./gate-batch-2.json \
  --format portfolio-csv --output ./private-projectoverzicht.csv

# De bestaande volledige tekstoverdracht bevat dezelfde projectsamenvatting.
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./gate-batch-1.json \
  --receipt ./gate-batch-2.json \
  --format markdown --output ./private-merge-eerst.md
```

**Concreet operatorgedrag:** per waargenomen repository toont het
HTML-projectpaneel en de CSV de hoeveelheid PR-kandidaten, P0/P1-aandeel,
onbekende gatebronnen, de verdeling van hoofdblokkades en alle gelijktijdig
waargenomen CI-, bron- en reviewhindernissen. De eerste bestaande
PR-kandidaat krijgt een link en expliciete menselijke vervolgactie.
De vollediger Markdown-overdracht begint nu eveneens met een projectgewijs
overzicht en behoudt alle afzonderlijke oorspronkelijke PR-kaarten.

De prioriteit is gebaseerd op de opgeslagen **hoogste gekoppelde
issueprioriteit**, met P0 boven P1 en meerdere kandidaat-PRs zichtbaar.
Dit is een **operatorvolgorde, geen claim, lease of releasebesluit**.
Als een project niet in het bronvenster voorkwam, wordt er geen
nulkandidaat, herstel of vrijgevallen werk uit afgeleid.
Ontbrekende batches blijven **ONBEKEND**. De bronmomenten, originele
reviewmanifest-/board-hashes en brongebonden privacygrenzen blijven
ongewijzigd; bestanden blijven create-only privé (`0600`,
`O_EXCL`, maximaal 1 MiB).

Dit is een concrete productfunctie voor handmatige portfolio-doorlevering.
Het start geen QA-/regressiesuite, GitHub workflow, CI-rerun,
agentclaim, VPS-commandoreeks, automatische broker-/herstelactie of
deploy. Alle exacte-head verplichte CI, onafhankelijke review,
branch protection en productie-eigenaarschap moeten nog afzonderlijk
worden gecontroleerd voordat een echte GitHub-mainmerge kan plaatsvinden.


### Machineleesbare overdracht voor een bevoegde runtime-eigenaar

Naast het menselijke HTML-, CSV- en Markdown-bord ondersteunt dezelfde
brongebonden CLI nu `--format portfolio-json`. Dit maakt een bruikbaar
machineleesbaar **adviespakket** voor vervolgcoördinatie zonder in een
bestaande portfolio-queue of Notion-claim te schrijven:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./review-manifest.json \
  --receipt ./gate-batch-1.json \
  --receipt ./gate-batch-2.json \
  --format portfolio-json --output ./private-zsentinel-owner-suggestions.json
```

Schema: `zsentinel-private-portfolio-work-suggestions-v1`.
De ontvanger krijgt originele bronmanifest-/board-SHA256, exact bewaarde
bronvenstertijden, batchdekking en ontbrekende batchindexen, plus
projecten in P0-eerst bronvolgorde. Per project worden oorspronkelijke
PR-kandidaten, P0/P1-tellingen, hoofd-/gelijktijdige blokkadecategorieën
en één bestaande PR-link met concrete menselijke vervolgactie geleverd.

De uitvoer is **niet** een dispatch- of mergecontract. Hij meldt
onomwonden dat de project-/PR-inventaris onvolledig kan zijn:
`known_complete_project_or_pr_inventory=false`,
`owner_claim_verified=false`,
`required_exact_head_ci_verified=false`,
`independent_reviews_and_branch_rules_verified=false`,
`worker_dispatch_authorized=false`,
`queue_mutation_authorized=false`,
`merge_authorized=false`, `deploy_authorized=false` en
`worker_recovery_verified=false`. Een bevoegde integrator moet zelf
actuele leases/owners, exact-head GitHub-gates, de serialized release
en echte productcontinuïteit opnieuw en onafhankelijk controleren.
De privacygrens is dezelfde create-only 0600 bronbestandsuitvoer, zonder
extern netwerk, browserautomatisering, AI-scheduler of nieuwe QA-rondes.


## 20 oktober · Complete project-PR werkrij uit opgeslagen gatebron

De offline portfolio-uitvoer behandelde tot nu toe per project vooral de **eerste**
PR-kandidaat. Daardoor kon een operator een volgend onafhankelijk, bestaand
product-PR binnen hetzelfde project missen, ook als dat al in dezelfde
gevalideerde reviewmanifest-batches stond. De bestaande
`scripts/zsentinel_saved_gate_batch_bridge.py` toont nu voor **ieder** project
alle daadwerkelijk geobserveerde PR-kandidaten, in P0/P1/P2/ongecategoriseerd
en gate-actievolgorde, met batchherkomst, afzonderlijke gelijktijdige
blokkades, gekoppelde originele issue-URL's en een concrete menselijke
vervolgstap.

Gebruik dezelfde, reeds lokaal opgeslagen en brongebonden bestanden:

```bash
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review-manifest.json \
  --receipt ./private-gate-batch-1.json \
  --receipt ./private-gate-batch-2.json \
  --allow-partial \
  --format html \
  --output ./private-project-desk.html
```

De kaart **Portfolio · bestaande PR afhandeling** heeft nu per project een
uitklapbare **volledige bestaande PR-afhandelrij**; `--format portfolio-csv`
levert naast het eerste PR ook het aantal overige kandidaten en de eerste
drie canonieke URLs. Een nieuw **kort, maar volledig per-project overdrachtsblad**
kan afzonderlijk worden gemaakt:

```bash
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review-manifest.json \
  --receipt ./private-gate-batch-1.json \
  --receipt ./private-gate-batch-2.json \
  --allow-partial \
  --format portfolio-markdown \
  --output ./private-portfolio-pr-handoff.md
```

`--format portfolio-json` bevat nu dezelfde per-project
`candidates`-rij met uitsluitend genormaliseerde, canonieke bronvelden.
Dat bestand is **niet** een worker-dispatchcontract of hoofdbranchvrijgave.
Een ontbrekende gate-batch blijft `missing_batch`; een bron zonder stabiele
HEAD blijft `source_unavailable_or_head_changed`. De link naar een bestaand
PR is **geen** bewijs van recente eigenaarstoestemming of dat een issue
opgelost is. De originele manifest-/board-SHA256, UTC-bronmomenten en
observatiebegrenzing blijven zichtbaar; er wordt niets opnieuw van GitHub
opgehaald.

Alle outputs lopen door de bestaande maximaal 1 MiB, create-only 0600
bestandsgrens en weigeren overschrijven. Geen testsuite, QA-run, claim, nieuwe
PR, service, CI-rerun, GitHub write, workerdispatch of productieactie wordt
door deze offline functie uitgevoerd. Voor merge eerst *apart* actuele claims,
exacte-head verplichte CI, onafhankelijke review, branch protection en
releasebevoegdheid vaststellen; alleen de echte main-SHA en live bewijs tellen.

Navigatie: boven de projectkaarten staat nu ook een toetsenbordtoegankelijke
**Ga naar project**-lijst met gewone interne HTML-ankers. Daarmee kan de
operator direct naar het relevante project en diens volledige, uitklapbare
PR-rij springen, zonder scripts, netwerkverzoeken of extra momentopnamen.


## 20 oktober · Zelfstandig projectdossier + complete PR-excelrij (PR #1560)

Een operator kan dezelfde al opgeslagen, exact gebonden bron nu **gericht**
voor één werkelijk in het manifest waargenomen project omzetten naar een
volledige offline werkpagina. Er is geen heel zCloud-dashboard of nieuwe live
API nodig. Dit is bedoeld om bestaande PR's af te handelen vóór nieuw werk.

```bash
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review-manifest.json \
  --receipt ./private-gate-batch-1.json \
  --receipt ./private-gate-batch-2.json \
  --allow-partial \
  --project Zennay/zCloud \
  --format portfolio-html \
  --output ./private-zcloud-pr-dossier.html
```

De pagina heeft een duidelijk **privé en opgeslagen bron**-waarschuwingspaneel,
bron- en manifest-SHA256, oorspronkelijke UTC-waarnemingsperiode, exacte
batchcoverage, aantal getoonde PRs ten opzichte van **alle** PRs in het
oorspronkelijke bronvenster en originele GitHub-issue-/PR-links. Elk project
heeft alle brongebonden PR-kandidaten met hun afzonderlijke menselijke acties.
Projectkaarten zijn toetsenbord- en printvriendelijk en gebruiken alleen lokale
HTML/CSS onder een strikte Content Security Policy, geen inline scripts,
externe afhankelijkheden, automatische login, browseropslag of API-oproepen.

`--project` werkt uitsluitend voor `--format portfolio-*`. Een project
zonder PR in de **gevalideerde bronsteekproef** wordt geweigerd in plaats van
foutief als leeg, veilig of gereed getoond. De selectie wordt *na* validatie
van het volledige manifest en alle aangeboden batches toegepast. Zonder
`--project` blijven de bestaande portfolio-exports portfolio-breed. Zowel
`portfolio-json`, `portfolio-csv` als `portfolio-markdown` accepteren
dezelfde selectie; JSON noemt nu expliciet het oorspronkelijke PR-volume,
het daadwerkelijk gefilterde volume en de optionele gekozen bronprojectscope.

Een aanvullende **volledige Excel-vriendelijke PR-afhandelrij** maakt
zelfstandige opvolging van alle bron-PRs mogelijk, in plaats van alleen
projecttotalen en maximaal drie links:

```bash
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review-manifest.json \
  --receipt ./private-gate-batch-1.json \
  --receipt ./private-gate-batch-2.json \
  --allow-partial \
  --project Zennay/zCloud \
  --format portfolio-pr-csv \
  --output ./private-zcloud-pr-actions.csv
```

Elke CSV-regel bevat prioriteit, exact origineel PR-adres, issue-adressen,
bronbatch, hoofd- én overige waargenomen gateblokkades, eventueel
daadwerkelijk waargenomen *oude* HEAD, en een concrete menselijke stap.
Actuele eigenaar, verplichte CI/review, merge op main en live blijven
**ONBEKEND**. CSV bevat uitsluitend canonieke GitHub-links, vaste
bronlabels, getallen en bron-SHA's — geen vrije operatornotities die als
spreadsheetformules kunnen worden uitgevoerd.

Alle formaten behouden de bestaande maximaal 1 MiB create-only 0600
bestandsgrens; ongeldige bronnen/filternamen krijgen geen output. Opgeslagen
beschikbare bronkandidaten vormen nooit een complete GitHub PR-inventaris,
actueel eigenaarsbewijs, leasevrijgave, benodigde review of merge-autorisatie.
Deze functie neemt niets over en verstuurt/dispatcht/retryt/merget/deployt niets.


## 20 oktober · Eén privé ZIP-projectdossier, zonder meerdere exports

Een volledig offline pakket met project-PR-rij, HTML, Markdown en veilige
bronidentiteit is nu met **één** bestaand CLI-commando te maken:

```bash
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review-manifest.json \
  --receipt ./private-gate-batch-1.json \
  --receipt ./private-gate-batch-2.json \
  --allow-partial \
  --project Zennay/zCloud \
  --format portfolio-zip \
  --output ./private-zcloud-handoff.zip
```

In het ZIP-bestand staan vaste, veilige namen:
`project-dossier.html` (zelfstandige CSP-gebonden offline werkpagina),
`project-actions.md` (alle bron-PRs en menselijke vervolgstappen),
`project-prs.csv` (volledige per-PR-tabel),
`source-receipt.json` (niet-autoriseren, met oorspronkelijke SHA256 en
getoonde scope) en `source-checksums.json` (SHA256 van elk van de vier
bronexports, tijdvenster, ontbrekende batches en expliciete `false` voor
claim/dispatch/review/merge/deploy/live).

Alle vier productweergaven worden uit **dezelfde al gevalideerde** opgeslagen
manifest/batches gebouwd, voordat het ZIP-bestand geschreven wordt.
Maximaal 1 MiB per entry, 4 MiB ongecomprimeerd totaal, en maximaal 1 MiB
opgeslagen ZIP. De bestaande create-only outputwrite (0600, O_EXCL,
O_NOFOLLOW) maakt óf één compleet bestand óf weigert, zonder bestaande
bestanden te overschrijven. De ZIP bevat geen uitvoerbare code, ZIP
path-traversal, ruwe GitHub-responses of extra externe data.

De inhoud is privé: verspreid hem niet in publieke GitHub-issues.
De bronstatus is **historisch en gedeeltelijk**, ook als alle opgeslagen
batches aanwezig zijn. Een aangevinkt of in ZIP vermeld PR is NOOIT
bewijs van actuele HEAD, vereiste CI, zelfstandige review, vrij eigenaarschap,
main-merge of productieherstel. Voor iedere echte merge of deploy blijven
de bestaande actuele eigenaar-, security-, branch-, CI- en releasegates vereist.


## 20 oktober · Privé projectdossier heropenen, doorzoeken en veilig uitpakken

De bestaande `portfolio-zip`-export is nu zonder oorspronkelijke manifest-,
gatebatch- of live GitHub-toegang te **inspecteren, per PR te doorlopen en
veilig uit te pakken**. De lezer is standalone Python-standaardbibliotheek
en schrijft bij alleen inspecteren niets.

```bash
# Inspecteer en toon maximaal 20 eerder waargenomen P0-PR's.
python3 scripts/zsentinel_saved_gate_dossier.py \
  --input ./private-zcloud-handoff.zip \
  --show-queue --project Zennay/zCloud --priority P0 --limit 20

# Koppel de overdracht expliciet aan een eerder betrouwbaar opgeslagen
# originele reviewmanifest-SHA256; maak uitsluitend een NIEUWE privémap.
python3 scripts/zsentinel_saved_gate_dossier.py \
  --input ./private-zcloud-handoff.zip \
  --expect-review-sha256 <64-tekens-SHA256-uit-eigen-betrouwbare-bron> \
  --extract-to ./private-zcloud-handoff-opened
```

De ZIP-lezer valideert éérst alle vijf exact verwachte namen, gecomprimeerde
en uitgepakte grootte, vaste reguliere bestandspermissies, alle vier SHA256
bestandschecksums en samenhangende bronidentiteiten/scope. De individuele
CSV/JSON/HTML/Markdown-output wordt pas daarna naar een nieuwe afgeschermde
map geschreven; elk bestand is 0600, de map 0700. Bestaande mappen,
bestanden of symlinks worden nooit overschreven. De parentdirectory en de
nieuw aangemaakte uitvoermap blijven via nofollow-bestandsdescriptors
vastgepind tijdens het schrijven; bij bestandsfouten worden alleen de eigen
nieuwe bestanden verwijderd.

Met `--show-queue` staan uit dezelfde ZIP de oorspronkelijke PR-URL, prioriteit,
afzonderlijke waargenomen bron/gate-status, originele issue-koppelingen en de
menselijke vervolgactie direct in de terminal. `--project`, `--priority` en
`--limit 1..50` filteren uitsluitend **de opgeslagen steekproef**. Zij
veranderen geen actuele GitHub PR of eigenaar. De tool weigert
contradictoire bron-PR-identiteiten, dubbele PR's of buiten de genoemde
scope gelegen selecties. Het ontbreken van een PR in het pakket bewijst
niets over de huidige GitHub-inventaris.

**Belangrijk vertrouwenverschil:** interne SHA256-controle bewijst dat
onderdelen van het ZIP-pakket bij elkaar passen, maar **niet** wie het
pakket heeft gemaakt. Gebruik desgewenst
`--expect-review-sha256` uit een afzonderlijk vertrouwde bron voordat je
de inhoud verspreidt of opent. Ook een volledig overeenkomend pakket
bewijst NOOIT huidig eigenaarschap, geldige verplichte CI, onafhankelijke
review, main-merge, vrijgegeven productie of werkelijk runnerherstel.
Geen diensten, Notion claims, workers, tests, workflows of deployments worden
door deze offline overdrachtsfunctie aangeroepen.


## 20 oktober · Twee complete privéprojectdossiers vergelijken — productvervolg

De bestaande `portfolio-zip`-bestanden zijn nu ook **zonder oorspronkelijke
reviewmanifests of losse gatebatches** chronologisch te vergelijken. De
nieuwe `scripts/zsentinel_saved_dossier_delta.py` hergebruikt de **bestaande
strikte ZIP-toelating** uit `zsentinel_saved_gate_dossier.py`. De vergelijking
leest alleen twee door de operator aangewezen, opgeslagen bestanden en voert
geen nieuwe GitHub, Notion, CI-, VPS-, queue- of workeractie uit.

```bash
python3 scripts/zsentinel_saved_dossier_delta.py \
  --before ./private-zcloud-eerste-moment.zip \
  --after ./private-zcloud-tweede-moment.zip \
  --format html \
  --output ./private-zcloud-pr-veranderingen.html
```

Andere beschikbare uitvoer: `--format markdown`, `--format json` of
`--format csv` voor spreadsheetgebruik. De uitvoer is een **nieuw**
create-only, 0600 privébestand van maximaal 1 MiB. HTML is Nederlands,
responsief, toegankelijk via gewone toetsenbordfocus, volledig offline,
scriptvrij en heeft een strikte CSP. CSV heeft een Nederlandse
puntkomma-/UTF-8-indeling en bevat uitsluitend canonieke PR-URLs,
vaste categorieën, bronhashes en menselijke standaardacties.

De functie vergelijkt **maximaal 300 PRs per dossier** en maximaal 600
verschillende PR-identiteiten, alleen bij exact dezelfde gekozen
projectscope en niet-overlappende UTC-bronmomenten in de juiste volgorde.
De originele board- en manifest-SHA256 worden per bron bewaard; als die
verschillen, staat dat uitdrukkelijk in het rapport. Wanneer ook het
oorspronkelijke bronmanifest verschilt, is de GitHub-steekproef dus niet
noodzakelijk gelijk gebleven.

Voor ieder origineel bestaand PR toont het resultaat:
- **Nieuw in de *tweede steekproef***, dus niet bewezen nieuw aangemaakt.
- **Niet meer zichtbaar in de tweede steekproef**, nadrukkelijk NIET
  bewezen opgelost, gemerged, gesloten of owner-vrij.
- **Gewijzigde waarneming:** exacte geobserveerde PR-HEAD, hoofdblokkade,
  gecombineerde blokkades, eventuele ontbrekende gatebatch,
  bronissuekoppelingen, prioriteit of menselijke vervolgstap.
- **Geen waarneembare verandering:** alleen tussen de twee opgeslagen
  bronnen; NIET gegarandeerd dat een huidige PR onveranderd is.

De portfolio-export bewaart hiervoor voortaan een beperkt, van de originele
gatebron afkomstig `gate_observations`-veld: afgeronde, lopende en
niet-succesvolle checks, waargenomen onafhankelijke approvals en
reviewverzoeken, legacy-statussen en bronpagina-volledigheid. De offline
projectpagina/Markdown-overdracht toont de belangrijkste aantallen, zonder
ze te promoveren naar bewijs van **verplichte** geslaagde CI of review.
Oudere eerder geëxporteerde ZIPs zonder dit optionele veld blijven
leesbaar: ontbrekende tellers blijven UNKNOWN.

Het veranderingsoverzicht ordent geobserveerde PRs altijd P0 vóór P1;
beperkte, brongebonden verschilsignalen bepalen vervolgens de handmatige
opvolging. Het maakt nooit een automatische workerclaim, restart, retry,
reviewapproval, GitHub-merge of release. Opnieuw actuele HEAD, claims,
required checks, protected-main-regels en onafhankelijk goedgekeurde release
controleren voordat een echte PR gemerged of gedeployed wordt.

Belangrijk: interne ZIP-checksums zijn geen bewijs van **authentieke afzender**;
alleen zelfstandig vertrouwde bronpins en actuele GitHub/Notion-autoriteit
kunnen dat vertrouwen aanvullen. Distributie van deze private rapporten
vraagt een aparte menselijke beslissing.


### Integratie · oude en nieuwe HEAD-cijfers nooit verwarren

De delta-uitvoer toont bij iedere gewijzigde PR-kaart een uitklapbare
**Vorige / Nieuwe bronwaarden**-weergave met oorspronkelijke geobserveerde
HEAD, gateklasse, beperkte CI-/reviewtellers en originele issue-URL's.
`gate_counts_same_exact_head` is uitsluitend waar wanneer **beide**
bronmomenten dezelfde niet-lege exacte PR-HEAD noemen. Als de HEAD is
veranderd of niet betrouwbaar bekend is, waarschuwt de operatorwerkbak
expliciet dat de twee gatecijfers niet als voortgang van dezelfde
codeversie te vergelijken zijn. Geen aantal voltooide checks is bewijs
dat de verplichte protected-main workflow groen is.

De zelfstandige `zsentinel_saved_gate_dossier.py --show-queue` laat nu ook
de begrensde CI-/reviewsteekproef per bron-PR zien en weigert ZIP-inhoud
waarin **een project of individuele PR zichzelf bevoegd verklaart**
(`merge_authorized`, `review_and_merge_authorized`,
`live_verified` of ontbrekende read-only metadata). Ook ongeoorloofde
of onbeperkte `gate_observations`-velden worden geweigerd. Dit is een
offline bronintegriteitsgrens, geen extra GitHub-releasegate.


## 20 oktober · zSentinel meerdaags PR-volgscherm (bestaande PR #1560)

De bestaande privé `portfolio-zip`-overdrachten kunnen nu per project
over **2–12 chronologisch opgeslagen bronmomenten** heen één
operatorwerkbak vormen. Dit is een concreet afhandeloverzicht van al
bestaande PR's: het groepeert oorspronkelijke GitHub-links, geobserveerde
HEAD-wijzigingen, eerder zichtbare gate-/issuewijzigingen,
steekproefafwezigheid en menselijke vervolgstappen in plaats van opnieuw
handmatig elke dag de opgeslagen dossiers te openen.

```bash
python3 scripts/zsentinel_saved_dossier_timeline.py \
  --source ./private-project-dag1.zip \
  --source ./private-project-dag2.zip \
  --source ./private-project-dag3.zip \
  --priority P0 \
  --change changed_gate \
  --page 1 --page-size 40 \
  --format html \
  --output ./private-project-historie-p0.html
```

**Gebruik:** herhaal `--source` twee tot maximaal twaalf keer, in
oplopende, niet-overlappende UTC-bronvolgorde. Alle dossiers passeren eerst
de bestaande strikte ZIP-bronadmissie; ze moeten dezelfde expliciete
projectscope hebben. Maximum 600 verschillende waargenomen PR-adressen.
De volledige bronhistorie wordt **eerst** gelezen; alleen het
menselijke eindbeeld wordt op P0/P1/P2, bron-veranderingstype en
pagina 1–100 (1–100 PR's per pagina, standaard 40) gefilterd.
Het resultaat vermeldt hoeveel echte **bron**-PR's er totaal,
gefilterd en op deze pagina getoond worden. PR's die niet op de huidige
pagina of in een bronvenster staan zijn nooit automatisch afgehandeld.

`--change` ondersteunt
`changed_head`, `changed_gate`, `changed_issues`,
`reappeared_in_sample`, `missing_between_samples` en
`last_sample_missing`. Een verdwenen PR is **niet bewezen gemerged**;
een PR die op opeenvolgende dagen hetzelfde gatebewijs toont, is
**niet bewezen vastgelopen**. Alle observaties zijn expliciet historisch.
Dit voorkomt dat een wisselende GitHub-zoekscope per ongeluk een
succes of release suggereert.

De output kan als één nieuw lokaal 0600-bestand (maximaal 1 MiB) worden
gemaakt met `--format html`, `markdown`, `csv` of `json`.
De HTML is responsief, Nederlandstalig, scriptvrij en CSP-afgeschermd.
Per PR zijn alle werkelijk waargenomen momenten uitklapbaar, met de
oorspronkelijke PR- en issue-links. De andere formaten geven dezelfde
menselijke prioriteits- en bronverloopinformatie; JSON bevat ook het
oorspronkelijke SHA256 voor **ieder** bronmoment.

**Cruciaal voor CI en reviews:** bron-HANDLE/HEAD-verschillen zijn
separaat van gatewijzigingen. Alleen wanneer beide steekproeven dezelfde,
niet-lege exacte HEAD zien, telt een gewijzigd gate-check/reviewcijfer
mee als een vergelijkbare bronwaarneming. Cijfers voor verschillende
of onbekende HEADs worden **niet-vergelijkbare receipts** genoemd
en vormen geen CI-voortgangsbewijs. Zelfs vergelijkbare cijfers geven
geen vrijgave voor verplichte CI, onafhankelijke review, branchregels
of worker-eigenaarschap.

Het volgscherm doet **geen** extra GitHub GET, Notion- of claimwrite,
queue claim, worker dispatch, CI-retry, Merge API call, zSSH mutatie
of productiedeploy. Het is een operatorhulpmiddel, geen geautomatiseerde
autorisatie. Verifieer merge of live herstel pas apart op actuele
geautoriseerde bronnen.


## 20 oktober · Issue-eerst bestaande product-PRs hergebruiken (PR #1560)

De nieuwe `scripts/zsentinel_saved_issue_timeline.py` vertaalt al
opgeslagen, volledig brongebonden PR-dossiers naar een **issue-eerst**
operatorwerklijst. Dat is de bruikbare route om een bestaande PR bij een
productissue te vinden voordat een nieuw werkpakket of PR wordt gestart.
Een los opgeslagen projectdossier is voldoende; met maximaal twaalf
chronologische dossiers is ook historische issue-/PR-ontwikkeling mogelijk.

```bash
# Eén bronmoment: meteen bestaande bron-PRs per issue zien.
python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./private-zcloud-handoff.zip \
  --priority P0 \
  --format html \
  --output ./private-p0-bestaande-product-prs.html

# Met meerdere bronnen en paginering; dezelfde gevalideerde bronfeiten.
python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./private-zcloud-moment1.zip \
  --source ./private-zcloud-moment2.zip \
  --page 2 --page-size 30 \
  --format csv \
  --output ./private-issues-pagina2.csv
```

De uitvoer groepeert de **werkelijk in een oude PR-steekproef
gekoppelde issues** op hoogste waargenomen bron-PR-prioriteit, met alle
betreffende bestaande PR-URLs, laatst waargenomen HEAD, oorspronkelijke
blokkade, laatst zichtbaar bronmoment, en een concrete menselijke
vervolgstap om actuele eigenaarschap/CI/review/branchregels te controleren.

PRs zonder betrouwbare issuekoppeling blijven **apart zichtbaar**, óók
als er een P0-filter voor het issue-overzicht wordt gebruikt. Afwezigheid
van een issue in de bron of van een PR in de laatste steekproef is geen
bewijs van een vrij issue, afgeloste blocker, gefixte code, bestaande
issue-eigenaar of succesvol gemergede PR. Een bestaand issue zonder
waargenomen PR wordt niet verzonnen.

`--priority` selecteert de hoogste waargenomen bestaande PR-prioriteit
voor de issuekaarten; `--page 1..100`, `--page-size 1..100` bieden
menselijke bronpaginering. De volledige bron wordt altijd eerst
toegelaten; een ontbrekende pagina wordt niet behandeld als ontbrekend
productwerk. Maximum 600 verschillende oorspronkelijke bronissues en
maximaal 12 bronmomenten. De HTML is lokaal, CSP-afgeschermd,
toetsenbordvriendelijk en zonder scripts of API-calls; de alternatieve
`--format markdown`, `csv` en `json` delen dezelfde bronaggregatie.
Het nieuwe resultaat is maximaal 1 MiB, schrijft alleen met create-only
en 0600 naar een nieuw privébestand en wijzigt nooit de bron-archieven.

Het issue-eerst scherm vervangt geen live GitHub-issuezoekopdracht,
actuele Notion-workclaim, verplichte exacte-HEAD CI, onafhankelijke
review of beschermde mainregels. Het **claimt of dispatcht niet** en
voert geen merge, runnerrestart of productieactie uit. Als een originele
PR niet bij het issue past, controleer de live bron en eigenaars voordat
je hem wijzigt.


## 20 oktober · Bestaande Product Delivery Workbench toont nu bron-PR's per issue

De **al bestaande interactieve operatorwerkbank**
`scripts/zsentinel_operator_workbench.py` accepteert nu optioneel één tot
twaalf reeds opgeslagen, strikte `portfolio-zip`-bronnen. Dit integreert
de eerdere issue-eerst PR-reusefunctie **direct** naast de bestaande
issue-interactie en menselijke overdrachten: geen losse vergelijkingspagina
meer nodig om een issue met zijn eerder gevonden product-PR's te verbinden.

```bash
python3 scripts/zsentinel_operator_workbench.py \
  --board ./private-product-delivery-board.json \
  --pr-dossier ./private-zcloud-pr-snapshot.zip \
  --output ./private-zsentinel-issue-en-pr-workbench.html
```

Voor historische PR-bronmomenten kan `--pr-dossier` maximaal twaalf
keer worden herhaald. Alle ZIPs passeren eerst de bestaande nofollow,
bronhash-, kanonieke PR-/issue-identiteit- en read-only checks.
Ze moeten dezelfde projectscope en oplopende niet-overlappende UTC
bronnen tonen. Originele board- en PR-ZIP-inputs blijven
**afzonderlijk gewaardeerde bronnen**, er wordt nadrukkelijk niet
beweerd dat ze afkomstig zijn van één actuele GitHub-scan.

In de interactieve werkbank staat vervolgens een extra bron-PR-paneel
met exacte koppelingen via de oorspronkelijke **canonieke GitHub
issue-URL**. Per overeenkomstige bestaande board-issue toont dit
paneel maximaal twaalf afzonderlijke bestaande PR-links, oude
bronprioriteit, laatst geobserveerde gateklasse en **het bronmoment
waarin de specifieke issue-naar-PR-link werkelijk gezien werd**.
Bijkomende PR's staan als niet getoonde bronkandidaten vermeld, nooit
als opgelost; andere historische issues en PR's zonder issuekoppeling
worden apart geteld en zichtbaar gemaakt. Deze gegevens zijn
uitsluitend historische **aanvullende context**; ze veranderen
geen bestaande human-handoff-notities, workerclaim, projectscope,
claimbesluit, merge of deploy.

Met alleen `--board` werkt de bestaande operatorwerkbank
ongewijzigd. De nieuwe invoer en uitvoer kunnen niet dezelfde
bestaande bronbestanden zijn. De HTML blijft privé, offline en zonder
backendwerk; verplichte CI/review/owner-protectieregels blijven
uitsluitend live te controleren. Een ontbrekende koppeling tussen
beide bronsoorten is **geen bewijs dat het board-issue geen open PR
heeft of dat iemand het eigenaarschap mag overnemen**.

Een zichtbare broncorrectie in de issue-eerst module bewaart nu ook
de juiste individuele waarnemingsmomenten per *specifieke* PR/issue-link.
Eerder kon PR X bij verschillende gekoppelde issues A en B ten onrechte
de gezamenlijke historiedatums laten zien. `observed_in_snapshots`
van issue A en B en de weergegeven `issue_ref_last_seen_snapshot`
komen voortaan uitsluitend van de bronmomenten waarin die relatie
zelf werkelijk gezien is. Ook het verdwijnen van de **link**, los van
het verdwijnen van de PR, betekent nooit dat het issue is gefixt.


### Zichtbaarheid voor niet-gekoppelde PRs

Ook de bron-PRs zonder terugvindbare issuekoppeling staan nu **met hun
echte oorspronkelijke GitHub-links** in een apart uitklapgedeelte van
de interactieve operatorwerkbank (maximaal dertig zichtbare links,
met expliciete telling van het niet getoonde restant). Ze zijn dus
niet alleen als aantal te zien en worden nooit als vrij, opgelost of
gemerged geclassificeerd. De werkbank blijft dezelfde private,
door de oorspronkelijke board/ZIP-bronnen begrensde HTML-output.

## 20 October · Private ZIP extraction without path detours (existing PR #1560)

The companion offline reader `scripts/zsentinel_saved_gate_dossier.py` now
opens a private handoff ZIP through an anchored, no-follow directory-descriptor
walk from the filesystem root. It refuses symlinks in **any ancestor**, not
only the immediate parent, as well as shared-write ancestors, an unowned
destination parent, a non-regular ZIP, and a ZIP that is not owned by the
operator or has group/other access. The exact descriptor bytes and inode
identity are checked before and after reading.

```sh
python3 scripts/zsentinel_saved_gate_dossier.py \
  --input /home/ubuntu/private-dossiers/source-project.zip \
  --show-queue \
  --limit 10

python3 scripts/zsentinel_saved_gate_dossier.py \
  --input /home/ubuntu/private-dossiers/source-project.zip \
  --extract-to /home/ubuntu/private-dossiers/extracted-project-20261020
```

The ZIP and destination parent must already be under private, operator-owned
paths that are not shared-writable. The new destination folder is created only
when it **does not exist**, with private owner-only mode. All five member files
must also have actual private owner-only modes; mounts that ignore 0600/0700
fail rather than pretending the dossier is confidential. Existing destinations
are never replaced. Console success output intentionally omits the private
destination path. The local operator remains responsible for keeping old
manually downloaded files secure.

The CLI also prints the **original sampled UTC instant** and a strictly
informational freshness classification: recent (under six hours), stale
(over six hours), uncertain clock, or unknown timestamp. A fresh timestamp
does not verify live GitHub status, exact-head CI, reviews, claims or release;
stale observations require renewed live source inspection before decisions.
No timed polling, merge/dispatch, automatic refresh, background work, network
requests, new QA suite or live production writes are introduced. Existing
review and CI gates remain unchanged.

### Saved PR inbox: urgent work first, fair between projects

The offline dossier CLI's `--show-queue` is now sorted by **observed**
priority P0 → P1 → P2 → unspecified. Within a priority, candidates from
different sampled repositories are interleaved round-robin by canonical
repository name, preserving each repository's original saved PR order.
One noisy project can no longer fill the first 20 lines solely because it
appeared first in an archived list.

The CLI states the exact filtered candidate total, sampled project count and
per-priority distribution before showing any PR. Its `--limit` applies
**after** priority/fairness ordering; missing rows are reported as not
displayed, never interpreted as merged or completed. This is strictly a
human-only navigation order over an old ZIP, not a real scheduler,
ownership validator, CI verdict or permission to merge. Combined with the
six-hour source-age label, it lets an operator find the most urgent known
work without treating snapshot omissions as an absence of live P0 incidents.

### One saved PR → one private owner handoff (20 October)

The offline project dossier reader now supports selecting a **specific existing
PR** by its exact canonical GitHub URL, regardless of its position in the
sampled, priority/fairness-sorted queue. It refuses unknown PRs and foreign
URLs rather than implying that a missing PR has been merged or released.

```sh
python3 scripts/zsentinel_saved_gate_dossier.py \
  --input /home/ubuntu/private-dossiers/portfolio.zip \
  --inspect-pr https://github.com/Zennay/zCloud/pull/1560 \
  --expect-review-sha256 YOUR_TRUSTED_64_HEX_MANIFEST_SHA \
  --handoff-markdown /home/ubuntu/private-dossiers/owner-pr1560.md
```

Both the input ZIP and handoff destination must be under existing private,
current-user-owned directories without symlink or shared-writable ancestors.
The Markdown file must **not already exist**. It is written to a new 0600
staging inode, fully fsynced, and published under the final name using a
create-only hardlink; existing output files are not replaced. Unsupported
mount permissions or hardlinks are rejected. The console displays only the
canonical public PR URL, original source-derived priority/blocker, number
of source-linked issues and a privacy-safe success message — no private
storage path.

The resulting readable Markdown includes source board/manifest SHA256,
original observation time, explicit stale/clock warning, incomplete batch
coverage, saved issue links, sampled gates and a bounded source next action,
plus human steps to confirm **current** PR HEAD, actual ownership, required
CI/review and protected-branch status in GitHub before any further work.
It is a **non-authorizing operator artifact**: neither a positive count nor
a recent snapshot grants ownership, validates exact-head required checks,
permits merge or proves production health.

For human navigation without a file write, omit `--handoff-markdown`.
`--show-queue` and `--inspect-pr` are distinct views and cannot be
combined. `--extract-to` is also unavailable with `--inspect-pr`, so a
targeted read/hand-off command does not unexpectedly extract the whole ZIP.
No new QA suite, network operation, runner dispatch, queue claim or
deployment is involved.


## 20 October · Merge-first issue-to-PR inbox from an existing private dossier

An operator can now start with the **existing issue** instead of remembering a
PR number. The already available private ZIP dossier reader groups only
canonical original issue links supplied by previously validated sampled PR
receipts, then exposes the related existing PR candidates:

```sh
python3 scripts/zsentinel_saved_gate_dossier.py \
  --input ./private-portfolio-dossier.zip --show-linked-issues --limit 20

python3 scripts/zsentinel_saved_gate_dossier.py \
  --input ./private-portfolio-dossier.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1518 \
  --handoff-markdown ./private-1518-review.md
```

The issue inbox is bounded, sorted by the highest observed PR priority, and
shows up to three PR links per displayed issue, with explicit omitted counts.
An exact source-linked issue can be opened to inspect up to 50 links on the
terminal and produce a **create-only private Markdown** handoff, containing
the archive's original SHA-256 identities, observation age, coverage gaps,
all original candidate PR references (up to 30 individually detailed),
sampled human follow-up steps and conservative partial gate observations.
`--project` and `--priority` can narrow the printed saved issue inbox,
but cannot turn it into proof of GitHub's current open issues.

The reader rejects nonexistent issue URLs, a canonical issue not linked to
any PR in this particular saved window, foreign repository identities, mixed
PR/issue selections, unsafe handoff paths and forbidden combinations of
inspection and extraction. It uses the existing validated ZIP reader and
private 0600 create-only Markdown publication; no extra provider request
or writable service interface is introduced. No branch, active claim or
production worker is touched.

**Delivery truth:** this is a historical, possibly incomplete issue-to-PR
navigator. It cannot tell whether a PR is still open, conflicts with an active
worker, has its exact-head mandatory CI complete, has an independent review,
was merged on main or is deployed. Operators must verify those directly in
GitHub/Notion and satisfy all existing gates **before** merging or assigning
work. No QA/regression suite was created or run.


### Direct issue-to-existing-PR desk inside the private HTML dossier

The already generated `project-dossier.html` inside the source-pinned
private ZIP now includes an **Issue naar bestaande PR · merge eerst** section,
before the ordinary project-by-project queue. This lets operators start from
a historically observed issue and open every displayed matching existing PR,
instead of opening another duplicate branch. It works without JavaScript,
network access, browser storage or extra permissions and uses only validated
original GitHub links from the same packaged evidence.

Issues appear by the highest sampled priority among their associated PRs;
each issue groups up to four original PR links and their observed primary
blocker. The view is bounded to 60 issue groups and explicitly reports
hidden source items, preserving the existing one-MiB dossier budget.
An issue absent from this section is **not resolved**: it may have no PR
reference in the sampled source or be outside the discovery window. A PR
present in the section is not necessarily open, unclaimed, CI-passing,
approved, main-merged or deployed. Do not treat a historical link as merge
permission. Review required source/owner/CI/security/production gates remain.

No QA/regression suite was written or run; implementation stays in the
existing zSentinel PR and does not modify the production dashboard or live
server.


## 20 oktober · Historische issue-continuïteit en gerichte overdracht

De bestaande `scripts/zsentinel_saved_issue_timeline.py` kan nu één
canonical issue selecteren uit één tot twaalf chronologisch gerangschikte
privé-ZIP-dossiers. Alle bronbestanden worden vooraf door de bestaande
strikte ZIP-lezer gecontroleerd. Dit is geen nieuwe live GitHub-bron.

```sh
python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./private-older.zip \
  --source ./private-newer.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1518 \
  --format html --output ./private-issue-1518-history.html

python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./private-older.zip \
  --source ./private-newer.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1518 \
  --format markdown --output ./private-issue-1518-handoff.md
```

De gerichte menselijk beoordeelde overdracht toont de bestaande
historisch gekoppelde PRs en hun laatst waargenomen issueverband.
Zij onderscheidt (1) een PR die helemaal buiten de eindsteekproef is
gevallen, (2) een PR die nog wel in de laatste steekproef zit maar waarvan
het issueverband niet opnieuw is gezien, en (3) meerdere bron-PR-kandidaten
die aparte actuele owner-/bestandsscopecontroles vragen. Afwezige
PRs of koppelingen bewijzen nooit dat werk is opgelost, gemerged of vrij.

HTML, Markdown en CSV bevatten de originele bronmomenten en volledige
bronmanifest-SHA256. Ontbrekende batches worden als brononzekerheid
gemarkeerd. Een niet-canonieke of niet in de bewaarde selectie voorkomende
issue-URL wordt geweigerd. Bij gerichte selectie worden ongerelateerde PRs
zonder bronissue niet in het private dossier weergegeven, en mag geen
tweede prioriteitsfilter of latere pagina worden gecombineerd.

Met slechts één bewaard bronmoment toont het programma alleen de huidige
historische steekproef, niet een verzonnen verandering. Huidige
GitHub-status, eigenaar, vereiste CI, reviews, protected-main, merge en
live deploy blijven onbekend zonder afzonderlijke verse bronverificatie.

Dit is een lokale 0600 create-only operatorfunctie zonder netwerk,
queue-/claimwijziging, CI-rerun, QA-suite of productieactie. De bestaande
verplichte CI, onafhankelijke review en releasebevoegdheden gelden onverkort.


### P0/P1 issueprioriteit blijft bij het oorspronkelijke issueverband

Een PR kan in latere opgeslagen snapshots naar een andere bronprioriteit
verschuiven of zijn oorspronkelijke issue-link verwijderen. De eerdere
issue-eerst lijst leidde dan de issueprioriteit uit de **nieuwste algemene
PR-prioriteit** af, zelfs wanneer die bronobservatie het issue niet langer
noemde. Dat kon een historische P0-issue onterecht lager plaatsen.

De huidige bronlezer bewaart nu per issue-link de hoogste prioriteit uit
uitsluitend de snapshots waarin de **exacte oorspronkelijke issue-URL**
werkelijk gekoppeld was. De nieuwste algemene PR-prioriteit blijft apart
zichtbaar in HTML, Markdown, CSV en JSON. Meerdere PRs bij hetzelfde issue
worden op de historisch gekoppelde issueprioriteit gerangschikt. Dit is
alleen een getrouwe historische triagevolgorde: een historische P0 is geen
bewijs van een nu nog open P0, en sluiting, eigenaarschap en merge worden
nog altijd niet automatisch geclaimd.


## 20 oktober · Eén offline issue-dossier als privé-ZIP

De issuegerichte historische hersteloverdracht kan met dezelfde bestaande
CLI direct als **één privé-ZIP** worden gemaakt. Dit voorkomt dat een operator
losse HTML-, Markdown-, CSV- en JSON-exporten per ongeluk uit verschillende
historische bronmomenten samenvoegt:

```sh
python3 scripts/zsentinel_saved_issue_timeline.py \
  --source ./private-older.zip \
  --source ./private-newer.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1518 \
  --format zip --output ./private-issue-1518-dossier.zip
```

De ZIP bevat vijf vaste onderdelen: een offline scriptvrij issue-dossier in
HTML, leesbare menselijke vervolgstappen in Markdown, een PR-matrix in CSV,
het exacte begrensde JSON-bronresultaat en een SHA256-manifest. Het manifest
pint de oorspronkelijke reviewmanifest-SHA256 per bronmoment en de exacte
bestandshashes binnen de ZIP; het verklaart expliciet dat live open-issues,
actieve claims, vereiste CI/reviews, merge en deploy **niet** geverifieerd zijn.

De bronnen zijn nog steeds strikt bewaarde private ZIPs. Alle uitvoer wordt
eerst in geheugen gemaakt voordat een nieuw, niet-bestaand lokaal bestand
met mode 0600 wordt aangemaakt. De ZIP heeft canonieke vaste paden,
niet-uitvoerbare mode 0600 en maximaal 1 MiB gecomprimeerde uitvoer.
Een specifiek issue-dossier laat ongerelateerde PRs zonder bronissue weg.
De SHA256-gegevens controleren interne inhoudsintegriteit, **niet**
authenticiteit van een live GitHub-providerrespons of toestemming om te mergen.

Er wordt niets aan GitHub, Notion, VPS, browser-workers of productie
geschreven. Het nieuwe formaat vervangt geen verplichte exacte-head CI,
onafhankelijke review, eigendomscontrole of beschermde main.


## 20 oktober · Bewaarde issue-dossiers veilig opnieuw openen

De `--format zip` issue-overdracht heeft nu ook een bijbehorende
**offline archieflezer**. Dit is een bedienbare productstap na de export:
een operator kan de bewaarde bron teruglezen en de oorspronkelijke HTML,
Markdown, CSV, JSON en checksumgegevens terugzetten als private bestanden.

```sh
# Bekijk eerder waargenomen issue- en PR-koppelingen; geen live GitHub-actie.
python3 scripts/zsentinel_saved_issue_archive.py \
  --input ./private-issue-1518-dossier.zip --limit 10

# Bekijk één exact oorspronkelijk issue in de bewaarde bron.
python3 scripts/zsentinel_saved_issue_archive.py \
  --input ./private-issue-1518-dossier.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1518

# Pak exact de eerder gemaakte volledige private ZIP uit in een NIEUWE map.
python3 scripts/zsentinel_saved_issue_archive.py \
  --input ./private-issue-1518-dossier.zip \
  --extract-to ./private-issue-1518-reopened
```

De archieflezer gebruikt dezelfde eerder beschikbare private no-follow
bestandsopening als de oorspronkelijke projectdossierlezer. Hij staat alleen
een eigen privébestand onder veilige parentmappen toe. De ZIP moet de
**vijf exacte canonieke leden** bevatten met niet-uitvoerbare 0600-metadata,
de verwachte vaste schemawaarden, correcte SHA256-matches en begrensde
uitgeklapte bestanden. Dubbele ZIP-leden of dubbele JSON-veldnamen worden
geweigerd. De JSON-bron moet expliciet iedere vorm van claim, dispatch,
main-merge, CI-review-vrijgave of live-herstel ontkennen. Issues en PRs
moeten canonieke links naar hetzelfde echte bronrepository bevatten;
een historisch issue met meerdere PRs blijft in de inspectie zichtbaar.

Een expliciet gekozen issue dat niet in dit archief voorkomt wordt niet als
vrij of gesloten behandeld, maar afgewezen. Gerichte `--inspect-issue`
mag alleen samen met `--extract-to` worden gebruikt als de ZIP zelf
al die **ene geselecteerde issuekaart** bevat; anders zou een ogenschijnlijk
gerichte operatie ongerelateerde privé-issuegegevens uitpakken. De
uitpakbestemming moet een nieuwe directory zijn (0700) en ieder bestand
wordt 0600, no-follow en create-only aangemaakt. Er wordt geen origineel
bronbestand overschreven of aangepast.

Het checksum-manifest bewijst alleen interne consistentie van de bewaarde
ZIP. Het is **geen providerhandtekening**, en een HTML-dossier is nooit
huidig GitHub-eigenaarschap, verplichte CI, review, een main-merge of
productiedeployment. De gebruiker moet deze bronnen zelfstandig live
controleren voordat hij eventueel bevoegd releasewerk uitvoert.

Deze feature maakt geen test-/QA-suite, netwerkcalls, workflow-retry,
nieuwe claim, broker-/security-/onderzoeksschrijfactie of production deploy.
Bestaande beschermde main-, review- en exacte-head CI-gates blijven intact.


### Gerichte menselijke overdracht vanuit een heropend dossier

Naast herstel van het complete offline dossier kan dezelfde archieflezer
nu uit een **exact geselecteerd oorspronkelijk issue** een nieuwe, korte,
brongebonden en private Markdown-owner-overdracht maken:

```sh
python3 scripts/zsentinel_saved_issue_archive.py \
  --input ./private-issue-1518-dossier.zip \
  --inspect-issue https://github.com/Zennay/zCloud/issues/1518 \
  --handoff-markdown ./private-issue-1518-owner-handoff.md
```

De overdracht bevat oorspronkelijke issue- en PR-links, de historisch aan
dat issue verbonden prioriteit, vroegere HEADs, opgeslagen observatiemomenten,
oorspronkelijke reviewmanifest-SHA256, ontbrekende batches en vier concrete
verplichte menselijke verificatiestappen. Vrije tekst uit de bewaarde JSON
wordt niet als CLI-advies overgenomen; operatorinstructies worden uit
beperkte gevalideerde bronvelden opgebouwd.

De archieflezer eist chronologische expliciete UTC-bronmomenten en
weigert ontbrekende, toekomstige of niet-chronologische bronmomenten
voordat hij een overdracht maakt. Een selectie moet precies één canonical
bronissue aanwijzen. Er wordt een volledig nieuw 0600-Markdownbestand
gemaakt met de bestaande private no-clobber writer; een combinatie van
dit handoff-bestand en de volledige ZIP-extractie in één CLI-run is bewust
uitgesloten.

Dit document kan door een operator beoordeeld en veilig intern
doorgegeven worden, maar bewijst **nooit** huidige PR-openstatus,
actief eigenaarschap, exacte-head CI, onafhankelijke reviews, protected-main
mergerechten of live productie. De originele source- en releasegates
blijven verplicht; er vinden geen externe systeemmutaties plaats.


### Archiefcontrole behoudt historische issue-/PR-betekenis

Bij het heropenen controleert de reader nu niet alleen SHA256 en canonieke
links, maar ook de **werkelijke onderlinge bronrelaties**: de lijst van
bronmomenten waarop elk issue bij iedere PR werd gezien, de laatste
waarneming van die issuekoppeling, PRs die buiten de eindsteekproef
vielen, en de afzonderlijke telling van links die verdwenen terwijl hun
PR nog wél in de eindsteekproef voorkwam. Ook de aantallen van meerdere
PR-kandidaten en de gemelde ontbrekende laatste batches moeten overeenkomen
met dezelfde bewaarde momentopnamen. Tegenstrijdige invoer wordt afgewezen
vóórdat de operator die als herstelbewijs kan presenteren of uitpakken.

Dit blijft een controle van onderling samenhangende historische
brondata, **geen GitHub-provider-authenticatie en geen QA-suite**.
Een geldig archief mag nog steeds geen vrijgave van eigenaarschap, CI,
review, merge of deploy suggereren.


## 20 oktober · Twee oude issue-ZIPs vergelijken zonder dubbele PR-implementatie

De bestaande privé-issue-dossiers kunnen nu als **brongebonden tijdsverschil**
worden gelezen via `scripts/zsentinel_saved_issue_delta.py`. De operator kan
eerst de originele geobserveerde PRs voor een issue zien en vervolgens direct
bekijken welke links in een latere opgeslagen bron zichtbaar, verdwenen of
gewijzigd waren. Er wordt **nooit** afgeleid dat een verdwenen PR is gemerged
of dat een issue is opgelost.

```sh
python3 scripts/zsentinel_saved_issue_delta.py \
  --previous ./issue-older.zip \
  --current ./issue-newer.zip \
  --issue https://github.com/Zennay/zCloud/issues/1518 \
  --format html --output ./private-issue-changes.html

python3 scripts/zsentinel_saved_issue_delta.py \
  --previous ./issue-older.zip \
  --current ./issue-newer.zip \
  --issue https://github.com/Zennay/zCloud/issues/1518 \
  --format markdown --output ./private-issue-changes.md
```

De huidige bron moet **strikt nieuwer** zijn. Beide dossiers worden volledig
gelezen en hun vijf vaste privé-ZIP-leden, SHA256-manifesten, canonieke
issue-/PR-koppelingen en expliciet onbevoegde status opnieuw gecontroleerd.
De bronselecties moeten dezelfde portfolioscope hebben en chronologisch
consistente, niet-overlappende of exact aaneengesloten gedeelde bronmomenten
bevatten. Een identieke bron kan binnen het ene dossier bijvoorbeeld
bronnummer 3 zijn en binnen het volgende bronnummer 1; het originele
reviewmanifest-SHA256 en de bronmetadata zijn leidend, niet zo'n lokaal
volgnummer. Onvergelijkbare bronnen worden geweigerd.

Het operatoroverzicht splitst de bronverschillen in drie duidelijke
categorieën: voor het eerst in het latere begrensde venster waargenomen PR,
niet meer in het latere venster waargenomen PR (onbekend, niet opgelost),
en PRs waarvan specifieke historische gegevens veranderden. Onder die
laatste categorie vallen onder meer de oude HEAD, hoofdblokkade,
bronprioriteit, issuegebonden prioriteit en de laatste geobserveerde
koppeling. De originele bron-SHA256, tijdstippen en ontbrekende batches
staan bovenaan de HTML-/Markdownoverdracht.

HTML is scriptvrij met een strikt lokaal CSP-beleid, originele canonieke
GitHub-links en een responsieve, leesbare weergave. Beide outputformaten
worden uitsluitend als nieuwe **0600 privébestanden** met geen-overschrijf
semantiek aangemaakt. Bij bronverlies of een ontbrekend issue in de nieuwe
beperkte steekproef volgt een weigering in plaats van fictieve
herstel- of mergeclaims.

Dit is operatorfunctionaliteit in de bestaande product-PR #1560. Er
worden geen CI-/QA-suites geschreven of gestart, geen nieuwe PR geopend,
geen actieve claims overgenomen en geen release- of VPS-systeem aangepast.
De actuele eigenaar, vereiste exacte-head CI, onafhankelijke review,
branch protection, main-merge en live deploy blijven afzonderlijk te
verifiëren vóór een geautoriseerde levering.


## 20 oktober · Portfolio-brede bronwijzigingen in één visuele herstel-inbox

Een operator hoeft niet langer iedere oude GitHub-issuekoppeling apart te
vergelijken. De bestaande privé-opslagroute heeft nu een **portfolio-brede
issue→PR delta-inbox** als complete uitvoerbare CLI en offline HTML-weergave:

```sh
python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./older-private-issue-portfolio.zip \
  --current ./newer-private-issue-portfolio.zip \
  --changed-only --format html \
  --output ./private-portfolio-issue-changes.html

python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./older-private-issue-portfolio.zip \
  --current ./newer-private-issue-portfolio.zip \
  --priority P0 --format markdown \
  --output ./private-p0-issue-handoff.md
```

De twee originele privé-ZIP-bestanden worden volledig ingelezen via de
bestaande strikte archieflezer; chronologie, portfolioscope, gedeelde
bronmanifesten en eventuele overlap worden met dezelfde admissionregels
gecontroleerd als de reeds gebouwde issuegerichte delta-CLI.

De inbox sorteert historisch waargenomen P0/P1/P2 eerst en onderscheidt:
- **Nieuw waargenomen issues** in het latere begrensde bronvenster; dit
  bewijst niet dat het issue werkelijk nieuw is geopend.
- **Issues niet meer in de latere steekproef**; dit bewijst zeker niet
  dat het issue is gesloten, dat de PR gemerged is of dat de eigenaar vrij is.
- **Gewijzigde issue→PR-relaties** waaronder oude bron-HEAD, blokkades,
  verschoven links en historisch issuegebonden prioriteit.
- **Ongewijzigde waargenomen bronrelaties**; dit blijft historische
  bronsamenhang, géén actuele succesvolle CI of merge.

De HTML bevat scriptvrije, responsieve PR-kaarten met directe canonieke
GitHub-bronlinks en per issue uitklapbare details. De CLI ondersteunt
`--priority`, `--changed-only`, `--page` en `--page-size`. De totale
broninventaris en filtermatches blijven vermeld, zodat paginering niet
per ongeluk 'opgelost' suggereert. Verwerking is bewust begrensd op 200
distincte historische issues en maximaal 60 zichtbare kaarten per pagina.
De originele SHA256-bronmanifesten, observatietijdstippen en ontbrekende
bronbatches staan bovenaan de bronmetadata van alle vier uitvoerformaten.

### Gestructureerde CSV- en JSON-overdracht

Dezelfde vergelijking kan nu ook als spreadsheet- of machineleesbare
overdracht worden opgeslagen, zonder een nieuwe bron op te halen:

```sh
python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./older-private-issue-portfolio.zip \
  --current ./newer-private-issue-portfolio.zip \
  --priority P0 --format csv \
  --output ./private-p0-issue-delta.csv

python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./older-private-issue-portfolio.zip \
  --current ./newer-private-issue-portfolio.zip \
  --repository Zennay/zCloud --changed-only --format json \
  --output ./private-zcloud-changed-issue-delta.json

python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./older-private-issue-portfolio.zip \
  --current ./newer-private-issue-portfolio.zip \
  --priority P0 --format zip \
  --output ./private-p0-issue-delta.zip

python3 scripts/zsentinel_verify_issue_delta_bundle.py \
  ./private-p0-issue-delta.zip
```

### Ontvangen ZIP controleren

De verifier leest het archief in het geheugen en pakt nooit bestanden uit. Hij accepteert uitsluitend de vijf verwachte leden, weigert dubbele/onverwachte paden, symlinks, encryptie en afwijkende interne permissies, begrenst de bestandsgrootte en decompressie en vergelijkt voor elk payloadbestand de SHA256 en grootte met `manifest.json`. Ook moeten de bronreceipts, actualiteitsmetadata, selectie en tellingen in het JSON-payload exact overeenkomen met het manifest; tegenstrijdige duplicaten of statusvelden die ten onrechte autoriteit claimen worden geweigerd. Het manifest moet expliciet melden dat actuele issue-status, eigenaar, verplichte CI/review, merge en live deployment niet zijn geverifieerd of geautoriseerd.

Een geslaagde verificatie bewijst **interne integriteit van de bundel**, geen identiteit van de afzender, authenticiteit van de bronmanifesten, actualiteit van brondata of toestemming voor release. Controleer die afzonderlijk aan de gezaghebbende bron voordat je handelt.

CSV bevat eerst één samenvattingsrij met de exacte manifest-SHA256's,
waargenomen UTC-tijden, actualiteitsclassificaties, ontbrekende batches,
filters, paginering en totale tellingen; daarna volgt één rij per getoond
issue. Ook bij nul filtermatches blijft de broncontext dus aanwezig. De
PR-lijsten en wijzigingsredenen staan als JSON-arrays in afzonderlijke
CSV-kolommen, zodat relaties niet door komma's of verkorte zichtbare kaarten
verloren gaan. Waarden met een spreadsheet-formuleprefix worden als tekst
geneutraliseerd. JSON bewaart
de volledige geselecteerde structuur, inclusief de bronreceipts en
expliciete **false**-waarden voor niet-geverifieerde issue-status,
eigenaarschap, CI/review, merge en live herstel. Beide formaten respecteren
de bestaande limiet van 48 KiB en vereisen respectievelijk .csv en .json.

ZIP bundelt de vier gerenderde bestanden plus manifest.json met de SHA256
en bytegrootte van elk lid, beide oorspronkelijke bronmanifesten, de
observatietijden en expliciete niet-autoriserende statusvelden. De vaste
interne bestandsnamen, 256 KiB-archiefgrens en create-only private writer
houden de bundel begrensd en voorkomen overschrijven of symlink-following.

CSV, JSON en ZIP zijn, net als HTML/Markdown, uitsluitend een privé-overdracht
uit twee eerder opgeslagen bronmomenten. Ze halen geen live GitHub-status
op en autoriseren geen claim, workflow, merge of deployment.

Uitvoer is alleen een **nieuw 0600 privé HTML-, Markdown-, CSV-, JSON- of ZIPbestand**.
In deze route vinden geen live GitHub-/Notion-/VPS-schrijfoperaties,
workflow-reruns, claims, QA-suites, merges of deployments plaats. Iedere
echte release blijft vereisen: huidige issue- en PR-status, actuele
eigenaar/claims, exact-head verplichte CI, onafhankelijke review,
protected-main en afzonderlijk toegestane productiehandeling.

Deze implementatie zit op de reeds bestaande product-PR #1560. Zelfs
als het offline dashboard werkt, is het **niet op main geleverd of live**
voordat GitHub daadwerkelijk merged=true en productieherstel bevestigt.


### Projectgerichte operatorfilter zonder historische P0's kwijt te raken

In de portfolio-brede issue-inbox kan een operator nu per bronrepository
filteren, bijvoorbeeld:

```sh
python3 scripts/zsentinel_saved_issue_portfolio_delta.py \
  --previous ./older-private-issue-portfolio.zip \
  --current ./newer-private-issue-portfolio.zip \
  --repository Zennay/Supa --priority P0 --changed-only \
  --format html --output ./private-supa-p0-source-changes.html
```

Het projectveld moet een canonieke `Zennay/repo`-identiteit zijn, geen
vrije zoekstring. De getoonde historische prioriteit wordt bepaald door
de **hoogste daadwerkelijk aan het issue gekoppelde prioriteit uit
beide bronsteekproeven**. Een P0-koppeling in de oudere bron die in de
latere bron P2 heet, blijft daarom onder de historische P0-waarnemingen
zichtbaar. De individuele oudere en nieuwere prioriteit blijven naast
elkaar staan, zodat dit niet wordt verward met een actuele GitHub-P0.

De gewone broninventaris en volledige samenvatting blijven zichtbaar
ook wanneer de operator maar één project, één prioriteit of één pagina
bekijkt. Nieuwe PR-claims, automatische werkdispatch en releases worden
hierdoor **niet** toegestaan.


### Niet-afgebeelde bronlinks zijn geen afgeronde reparaties

De portfolio-HTML toont per uitklapbare groep de eerste drie oorspronkelijke
PR-links en het **exacte aantal extra niet-afgebeelde PRs**. De
Markdownoverdracht toont de eerste vijf en meldt eveneens hoeveel er
achterwege blijven. Dit voorkomt de foutieve indruk dat alle reparaties
voor een issue op een korte kaart zichtbaar zijn. De overzichten blijven
gebaseerd op één door de operator gekozen pagina, terwijl de broncijfers
en alle filtermatches behouden blijven. Weggevallen of verborgen items
zijn **niet** bevestigd als gesloten, gemerged of vrijgegeven werk.

Voor volledige inspectie van één issue gebruikt de operator de reeds
beschikbare `--inspect-issue`-route van
`zsentinel_saved_issue_timeline.py` en inspecteert daarna actuele
GitHub/Notion-eigenaarschap en verplichte exacte-head mergegates.


## Project recovery without an issue card (21 October integration-runtime)

The existing board records **project_operations** separately from issue cards.
The Workbench now keeps this previously lost, human-only product route visible.
Expand **Project recovery · issue-independent source observations** above
the issue filters to inspect every admitted project's original sampled issue
counts, source coverage, bounded queue pressure, claim-observation uncertainty
and recommended human investigation. A project with **zero observed issues**
can still display an expired-lease or claim-cycle investigation lead; zero
issues never means a healthy or currently idle worker. Source coverage is
always incomplete, and queue attempts are not verified failures.

**Preview current filtered briefing** and the reviewed private Markdown
export now also contain a clearly separate **FULL saved portfolio snapshot**
section with those project-level leads. This intentionally remains
portfolio-wide even when issue filters show a narrow selection; the heading
states that distinction. A project with no issue card can therefore still
produce a useful source-bound human handoff. The export does not assert an
available claim, recovered runner, released lease or deploy.

The reader validates the exact bounded project-level counts, lease-pressure
aggregate, unique project identities, incomplete-inventory flag and
non-authorizing action flag. It checks issue coverage against the admitted
source summary. Only a coarse, non-authorizing **claim observed / not
verified** indicator can reach the browser; opaque claim IDs, task titles,
raw SQLite fields, worker prompts and credentials cannot.

All of this is local rendering and optional human export of the existing
immutable source. It does not query Notion, GitHub or the live VPS, start CI,
mutate queues/claims, assign a worker, merge a PR or deploy anything. Existing
independent review and exact-head mandatory CI remain required before any
main-branch integration. No new QA suite or manual test run was introduced.


### Queue source clocks and ranked human follow-ups (21 October follow-up)

The project recovery desk now sorts already-admitted **human investigation leads** as follows: potentially expired leases (owner reconciliation required), P0 product blockers, recurring claim cycles, P1 product blockers, and routine source rechecks. The priority is a *visual review order*, never a recovery or dispatch order. Each visible queue-pressure figure is accompanied by its **original queue source timestamp in UTC**, even if the later board and HTML report were generated at another time. Queue items without an accepted fresh retry-source timestamp are rejected rather than silently presented as valid current evidence. The private Markdown handoff includes the original timestamp and the same non-authorizing warning, including for projects with zero issue cards.

Original source clock and freshness flags must match exactly, with no unknown source families, future capture or missing retry provenance for admitted queue pressure. Empty or outdated queue sources remain **UNKNOWN**, not healthy. The page still never asserts active ownership, true failure counts, permission to reclaim expired leases, or any successful merge/deploy. All existing branch CI, review and real production gates stay unchanged; nothing was dispatched or executed as a QA suite.


### Private project-scoped recovery handoff (21 October continuation)

The **Project** filter is now shared by the saved issue cards and the
issue-independent Project recovery panel. Choosing a project hides other
projects' recovery leads without changing their admitted evidence. The
panel's visible-count indicator follows this local selection; users can
select a project even if there are no issue cards for it.

The explicit **Preview current filtered briefing** route also applies the
selected project to project-level recovery leads, while other issue filters
(search, priority and human disposition) continue to apply only to issue
cards. With no Project filter, its heading warns that the full saved
portfolio is included. With a Project filter, the handoff clearly names the
selected project and does not export other projects' queue-pressure context.
A filtered project with zero captured issues can still produce a private
human-only recovery briefing from its admitted project operation. This is
particularly important for recovery work that has no P0/P1 GitHub issue.

All source data remains immutable and local. The project selection neither
confirms ownership nor dispatches tasks, reclaims leases, alters gate states,
requests CI, merges or deploys. Selecting no visible issue card is never
proof that the project has no unfinished work.

### Local project incident actions and private JSON handoff (21 October)

The issue-independent project recovery panel now allows a **human-only** per-project
decision: Investigate, Coordinate current owner or Park external blocker.
A separate optional operator note (up to 500 characters) records the perceived
cause, required owner input or the next manual follow-up. These annotations exist
only in the open browser tab; editing them invalidates any previously reviewed
Markdown preview and warns before leaving with unsaved edits. Nothing is stored
in localStorage or transmitted to a service.

The **Download private human handoff** action now saves both the existing
issue annotations and the new project annotations in a single explicit
`schema_version: 4` local JSON file. Every record must match an actual
project from the exact original read-only board SHA-256. The saved file continues
to carry `authorizes_claim=false`, `authorizes_merge=false` and
`authorizes_deploy=false`; no local decision is a real Notion task state.
Project annotations are bounded, identity-checked, duplicate-refused and
validated *before* replacing any current browser notes. Existing handoff
versions 1–3 remain importable for their original issue annotations; missing
project follow-ups remain blank. Cross-board or malformed imports are refused
without partially changing the current notes.

The existing **Preview current filtered briefing** now includes the human
decision and private note for each selected project's recovery leads. Each
project incident card provides **Preview private recovery handoff for this
project**, which first selects that project, then opens the existing reviewable
private Markdown preview. No file is downloaded without the separate explicit
download action. The JSON handoff intentionally backs up **all** annotated
issues and projects belonging to the exact source board, whereas the filtered
Markdown handoff includes only the selected project's recovery leads.

Earlier-source manual carry-forward can still reuse matching issue follow-ups
from a v4 handoff, subject to its existing explicit review. Project incident
notes are deliberately **not** auto-carried across a new board SHA; a live
lease, original queue timestamp and ownership must be rechecked by a human.
No network, CI dispatch, queue write, lease reclaim, workflow replay,
service restart, production deployment, broker action or QA suite is created
by this source-only UI.

### Saved exact-head PR evidence next to the original issue (21 October)

The original product-delivery board already held canonical
`pr_delivery_observations` and per-issue `pr_delivery_candidates`, but
the Workbench used to discard these fields. This required a separate
offline ZIP to see even previously saved PR gate information.

Now each original issue's **Existing PR references** displays the
previously captured GitHub PR HEAD, original PR observation UTC timestamp,
observed native CI success flag, available legacy-context status,
observed independent approvals, previously reported mergeability
and recorded unresolved gate labels. The *same* bounded read-only facts
are included in the reviewed private Markdown issue handoff. If a PR
reference lacks a matching admitted observation, the desk explicitly says
the source is missing instead of assuming checks succeeded.

The offline input is fail-closed: at most five unique canonical PR
observations, 40-character immutable source HEADs, a known UTC PR source
clock and explicit non-authorizing flags. Every per-issue PR observation
must agree exactly with the previously validated top-level PR inventory;
cross-issue or mismatched PR gates cannot silently replace source evidence.
No arbitrary GitHub metadata or credentials enter the browser.

**These are historical snapshots, not merge eligibility.** In particular,
a saved success flag, apparent mergeability or prior approvals do not
establish current required checks, branch protection, an active owner,
the present PR HEAD, consent, source merge or a live deployment. The
operator must independently refresh all of those current facts from GitHub
before any privileged action. No new API call, QA suite, runner dispatch,
claim, merge or deploy is introduced.

### Manual UTC project follow-ups and source-bound v5 handoff

The issue-independent Project recovery desk now supports a **manual contact
label** (80 characters, never an actual Notion/GitHub assignee), a **UTC review
date** (ISO calendar day), a human-only triage decision and a short private
operator note. Empty contact/date values do not imply that a worker is free.

For each visible project, the desk displays whether a local follow-up is
overdue, due today, planned for later, awaiting contact information, or
unscheduled. Its overview aggregates **overdue / due-today / missing-contact**
counts for the current project selection. The counts only describe private
local notes; they are not scheduler state, a timed notification, a live owner
probe, or a gate to claim or retry work. The reviewed Markdown project handoff
contains the human contact label, UTC review date and the same explicit
non-authorizing status.

The source-bound private JSON format advances to `schema_version: 5` to keep
project `owner` and `due` alongside the existing decision/note and issue
annotations. Import of earlier v1–v4 files is still supported: v4 project
decisions and notes restore with empty manual contact/date; v1–v3 restore
their original issue annotations. For v5, imported project IDs must belong to
the **same exact board SHA**, be unique and bounded, have a known decision,
plain operator note, normalized contact label and valid UTC review day.
All records are validated before either the project or issue maps are
replaced. Invalid/foreign files cannot partially clobber local work.
Project notes never carry automatically across newly generated board
snapshots: live claim, incident and lease evidence must be reviewed again.

No new tests, test dispatch, bot/worker assignment, CI bypass, persistent
storage, network request, automated reminder, service restart, queue write,
GitHub merge or production deployment is introduced.

### Issue-independent manual recovery calendar export (21 October)

The existing **Preview reviewed calendar events** route now also includes
project-level recovery follow-ups for projects with no issue cards. As with
existing issue follow-ups, a project must have a locally entered, valid
UTC review date **and** an explicit human contact label before it appears
in the private all-day ICS export. The accepted project name is the
validated identity from the original saved board, not a queue task ID
or active claim. The calendar description records the source board timestamp,
the original queue-source timestamp or UNKNOWN, and the human-only disposition.

For privacy, private notes, issue-free queue identifiers and any presumed
live assignee remain excluded from the calendar. The manually entered
contact label is omitted by default and is included only after the existing
explicit contact opt-in checkbox is selected. The entire resulting ICS is
visible for review before the separate local download action. The combined
offline export is capped at 60 all-day events; known manual issue entries
retain priority and project entries fill the remaining bounded slots.
No notification, automated reminder, third-party calendar API, email or
live project action is invoked. Calendar import may have its own
notification/sync defaults outside this tool; the user must decide whether
and where to import the downloaded file.

The private JSON handoff format remains v5 and stores the human project
contact/date for later manual restoration on the **same board**. These
local timestamps are not evidence that an original GitHub issue, worker,
lease, CI job or production deployment is healthy or resolved.


## Recovery policy request from a saved issue

Selecting an issue now exposes a **Recovery policy request** section with an explicit proposed-action selector and a private JSON download. This makes the policy contract accessible from the existing visual operator workbench without requiring the operator to copy identifiers by hand. The selector includes parking one dependent task when an external blocker is unchanged, while explicitly requiring disjoint ready work to continue; it never parks an entire project lane. It also supports escalation to a named human only for a specific permission requirement, an irreversible/high-risk decision or exhausted safe routes; the corresponding evidence remains unknown until supplied.

The generated request uses the saved canonical GitHub issue/PR URL and the original issue-inbox observation timestamp. It does not replace that timestamp with the current browser time, so stale saved evidence stays stale when evaluated. Project and incident identifiers are normalized to bounded slugs; owner remains null; every policy evidence field remains null/unknown. The request therefore defaults to HOLD until the operator records actual evidence.

The export is local-only: no GitHub/Notion request, claim write, CI retry, merge, deployment or runner restart occurs. The policy evaluator's positive result is only READY_FOR_HUMAN_REVIEW, never action authority. The generated JSON is compatible with scripts/zsentinel_recovery_policy.py on the companion recovery-policy branch.

## 21 October · continue with another project when one recovery lead is parked

The project-recovery desk now has **Next non-parked project candidate (P0 first)**.
It skips only projects that the operator has locally marked `park-blocker`;
it does not infer that any other project is free, unowned, healthy, or safe to
mutate. The queue prioritizes sampled P0 projects, then P1, then manually
recorded review-date urgency and observed queue pressure. The summary separates
non-parked candidates from locally parked projects and explicitly says that
current PRs, active claims, branch rules and owner authority must be rechecked.

Selecting a candidate clears the project filter, opens the recovery list,
scrolls to that project's human follow-up controls, and focuses its local
decision selector. Existing issue-level notes are saved before navigation.
This avoids remaining stuck on one locally parked recovery lead while making
the next project visible; it is navigation only, not a worker dispatch or
claim release. Local `park-blocker` status is not proof that the original
incident is resolved.


## 21 October · Complete offline issue-to-PR relationship handoff

The existing saved-gate bridge now supports `--format portfolio-issue-pr-csv`
(with optional `--project Zennay/zCloud`) alongside the existing
`portfolio-pr-csv` and `portfolio-zip` outputs. This is a **complete
issue-to-PR matrix within the already admitted saved review snapshot**:
one row per observed canonical issue/PR relationship, not just one row per
PR or the first four links in the HTML issue preview. The existing project
ZIP additionally includes `issue-pr-matrix.csv`; its SHA256 is recorded in
the existing `source-checksums.json` along with the other payloads.

For example, with the same original saved manifest and `--receipt` flags
used by other portfolio exports:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py \
  --from-review ./private-review.json \
  --receipt ./private-gate-batch-1.json \
  --format portfolio-issue-pr-csv \
  --output ./private-issue-pr-matrix.csv
```

Supply all expected saved receipts, or explicitly use the existing
`--allow-partial` mode: missing batches remain **UNKNOWN**. A project
filter is applied *after* full source admission. Rows preserve the original
review-manifest/board digests, observed UTC source interval, historical
source HEAD or ONBEKEND, gate-source classification and human-only next
action. They say that current GitHub state, active owners, required CI,
review, main merge and live recovery are **ONBEKEND**, not approved.

The offline issue index in the project HTML now shows **all five**
source-admitted PR references for a single issue (the existing admission
limit is five), ordered by observed priority and gate reason. A shared issue
is not evidence of a file conflict, ownership transfer or fix. The CSV and
ZIP are private create-only 0600 output with bounded size and SHA256
receipts. Neither format performs live GitHub/Notion queries, tests, claims,
retries, merges, production writes or deployments.


### Focus on one source-linked issue, without losing competing PRs

The same offline bridge now offers a **single-issue** private dossier in four
compatible formats: `portfolio-issue-markdown`,
`portfolio-issue-html`, `portfolio-issue-csv` and
`portfolio-issue-zip`. Pass an exact `--issue-url` that actually occurs
in the admitted saved review manifest. The complete original sources are
validated before the issue is selected; foreign or unobserved issues fail
closed instead of appearing free.

For example, using the same saved review and gate receipt inputs:

```sh
python3 scripts/zsentinel_saved_gate_batch_bridge.py --from-review ./private-review.json --receipt ./private-gate-batch-1.json --format portfolio-issue-zip --issue-url https://github.com/Zennay/zCloud/issues/1326 --output ./private-issue-1326-handoff.zip
```

Only choose an issue actually present in those saved source receipts. The
issue ZIP bundles `issue-pr-dossier.html` (script-free offline browser
page), `issue-pr-actions.md` (human follow-ups for every linked PR),
`issue-pr-matrix.csv` (each admitted issue/PR relationship),
`source-receipt.json` (canonical issue/repository, observed source PR/HEAD,
original digests, UTC interval and explicit gate unknowns) and
`source-checksums.json` (SHA256 per original member). The existing
create-only 0600/no-follow writer and size limits remain unchanged.

Several PRs may mention the same source issue. The issue-specific output
shows each separately, P0 first then by the shared gate classifier. A shared
issue is **not** proof of a fix, file conflict, current ownership or review.
Missing batches remain UNKNOWN. The owner must refresh current GitHub HEAD,
mandatory CI/review, branch protection, active Notion/queue claims and
release permissions before any privileged action. These exports do not run
QA suites, dispatch CI, read live providers, claim workers, retry blocked
jobs, write production state, merge or deploy.


## Saved-board comparison includes exact-head PR gate drift

The two-snapshot issue comparison now compares the validated saved PR gate
receipts as well as issue priority/title, PR links and evidence gaps. For a
canonical PR URL that remains linked in both snapshots, it reports separately
whether the observed HEAD changed, native/legacy check or blocker evidence
changed, observed approval count changed, or the sampled mergeability value
changed. It also flags when a gate receipt is newly present or no longer
available; a missing receipt is **UNKNOWN**, not success or resolution.

The baseline import admits gate receipts only when they match the earlier
board's bounded top-level PR observation inventory and its original PR source
clock. The captured timestamp must not postdate that earlier board, and the
source must have been marked fresh at capture. It does not fetch GitHub or
re-evaluate freshness at import time. A changed receipt is only a change in
saved observations: current PR head, required branch rules, live owner, actual
review state, mergeability and release permission must still be checked
independently. The existing two-snapshot CSV includes these differences in
its `changed_source_fields` column without carrying private human notes.


## Project-level recovery deltas between saved boards

The offline comparison also admits the bounded `project_operations` inventory
from the earlier board. It compares per-project issue-source coverage, sampled
P0/P1/P2/unranked counts, aggregate queue pressure (active items, high claim
cycles and expired leases), reported claim-context presence, and the recommended
human investigation. A queue sample is admitted only when its original source
clock is valid and no later than the earlier board; claim identifiers are never
carried into the comparison. A timestamp refresh alone is not treated as a
state change when the sampled values are unchanged.

New project observations, changed recovery signals and projects no longer
visible are listed separately from issue/PR changes. Disappearance remains
NOT resolved. A separate reviewed project-recovery CSV includes both exact
board SHA-256 values, captured observation times, coverage, prior/current
aggregate values, source clocks and explicit false claim/merge/deploy authority
columns. It excludes private human notes and raw claim IDs. The existing issue
delta CSV remains issue-scoped and now includes saved PR-gate/HEAD differences
inside its `changed_source_fields` column.

Neither comparison refreshes data from GitHub or Notion. All gate and queue
values are historical saved observations; they do not prove current PR state,
worker ownership, queue availability, merge clearance or release authority.


## 21 oktober · historisch issue eerst volgen door meerdere PR-dossiers

De bestaande offline `scripts/zsentinel_saved_dossier_timeline.py` kan
een reeks van twee tot twaalf chronologisch gesorteerde, reeds afgesloten
private PR-dossier-ZIP's vergelijken. Het product had wel een algemene
`changed_issues`-tag, maar kon niet laten zien *welke* issuekoppelingen
toegevoegd of niet meer geobserveerd waren. Ook kon de operator niet meteen
de PR-historie van een geselecteerd productissue isoleren.

Die twee onderdelen zijn nu geïntegreerd op de bestaande product-PR #1560:

- Per opeenvolgende werkelijk **geobserveerde** PR-bron: exacte canonieke
  issue-URLs die later wel, respectievelijk niet meer gekoppeld waren.
  Een overgeslagen steekproef staat expliciet gemarkeerd: het moment waarop
  een koppeling daadwerkelijk veranderde blijft ONBEKEND.
- JSON bewaart per PR de geobserveerde nieuwe/niet-meer-gekoppelde
  issue-URLs, bronindexen en beide cumulatieve aantallen. De bestaande
  Markdown, scriptvrije HTML en Excel-leesbare CSV tonen dezelfde
  bronverschillen. Canonieke issue-links in HTML openen alleen de
  oorspronkelijke GitHub-issues; er is geen JavaScript of netwerklezing.
- Nieuw `--issue https://github.com/Zennay/zCloud/issues/1518` beperkt
  de weergave tot PRs die in **minstens één** van de opgeslagen
  bronmomenten aan dat issue waren gekoppeld. Combineer met de al bestaande
  `--priority`, `--change` en paginering: de volledig ingelezen
  broninventaris en bron-SHA's blijven ongewijzigd.

Voorbeeld op een al vertrouwde operatorhost met reeds opgeslagen bronnen en
een private bestaande map:

```sh
umask 077
python3 scripts/zsentinel_saved_dossier_timeline.py \
  --source /private/zsentinel/pr-a.zip \
  --source /private/zsentinel/pr-b.zip \
  --issue https://github.com/Zennay/zCloud/issues/1518 \
  --format html --output /private/zsentinel/issue-1518-pr-history.html
```

**Geen bewijs van afronding:** de oude dossiers worden offline gelezen,
zonder nieuwe GitHub-/Notion-/queue-query. Een PR die uit een steekproef
verdwijnt is niet bewezen gesloten of gemerged; een issuekoppeling die
verdwijnt is niet bewezen opgelost; een nieuwe koppeling is geen bewijs
van open issue-ownership. De operator moet vóór actie actuele GitHub HEAD,
verplichte CI, reviews, protected-main, Notion/queue-eigenaarschap en
releasebevoegdheid onafhankelijk bevestigen. Deze uitbreiding maakt geen
claim, dispatch, merge, CI-herstart, VPS-mutatie of deploy mogelijk.


### Gegevens bij issuegerichte bronselectie

Bij `--issue` bewaart elke geselecteerde PR-kaart nu aanvullend
`selected_issue_source_history` met het eersteklas, exacte issue-URL,
de chronologische bronindexen waarin de link voorkwam, het laatste
bronmoment mét die koppeling, en twee verschillende booleans:
`linked_in_last_observed_pr_snapshot` en
`linked_in_latest_source_snapshot`. De laatste is uitsluitend waar als
zowel de PR als de issuekoppeling voorkwamen in de laatste ingelezen
bronsteekproef.

Dat onderscheid voorkomt dat een operator een oude PR met historische
issuekoppeling voor actuele open issue-ownership aanziet. JSON, HTML,
Markdown en CSV hebben dezelfde waarheidsgrens: verdwenen PR uit een
begrensde bron, verdwenen issuekoppeling, oud HEAD of afgenomen
checktellingen betekenen **niet** dat de fix op `main` staat.
`current_issue_resolved_verified=false` en
`live_owner_verified=false` blijven harde onderdelen van deze export.
De geobserveerde bronkaarten creëren geen review, actiebevoegdheid of
claim. Vóór een daadwerkelijke PR-merge of herstelactie zijn nog steeds
verse originele GitHub-/Notion-/queue-receipts en de bestaande
verplichte gates vereist.


### Oorspronkelijke bronintegriteit en ontbrekende archiefbatches

De issuegerichte PR-historie toont nu ook in de gewone offline HTML het
originele **reviewmanifest-SHA256** en **board-SHA256** per bronmoment, de
vroegste/laatste opgenomen UTC-bronklok, de geselecteerde PR-kandidaten
en alle door de archiefbron gemelde ontbrekende batchindexen. Deze gegevens
zijn ingeklapt maar direct bereikbaar in het interactieve, scriptvrije
`Herkomst van de originele bronbestanden`-gedeelte.

Wanneer minstens één bron een ontbrekende batch noemt, waarschuwt het
historieoverzicht daar expliciet voor. De JSON heeft
`source_batches_missing_in_any_snapshot`; Markdown vermeldt de
bronintegriteit per invoer en CSV draagt ook de originele manifest-hashes
en ontbrekende-batchindexen. **Geen ontbrekende batch gemeld** betekent
uitsluitend dat de opgeslagen dossiers dit niet melden; het is geen
bewijs van complete of recente GitHub/Notion/CI-dekking.

Hierdoor kunnen menselijke reviewers een uitgefilterde issue/PR-kaart
terugvoeren op zijn originele privébron zonder verloren linkgegevens of
onzichtbare steekproefgaten als afgeronde issues te presenteren. De
bronbestanden worden niet gewijzigd. Deze functie is uitsluitend lokale,
handmatige leesweergave en verleent geen claim, merge, deploy of
runnerherstelfunctie.


## 21 oktober · issuegerichte hersteloverdracht over meerdere archiefmomenten

De bestaande [zSentinel PR #1560](https://github.com/Zennay/zCloud/pull/1560)
breidt de privé **issue-first PR-geschiedenis** uit tot een compleet
operatoroverzicht. De selectie `--issue` blijft beperkt tot exacte,
canonieke GitHub-issue-URLs en leest uitsluitend maximaal twaalf reeds
opgeslagen, chronologisch brongebonden ZIP-dossiers. Er worden geen
nieuwe live GitHub-, Notion-, queue- of servicebronnen geraadpleegd.

De eigenaar ziet nu op één plek, berekend over **alle gefilterde PR-kandidaten
en niet alleen de zichtbare pagina**:

- het aantal PRs dat ooit in de opgeslagen steekproeven aan dit issue
  gekoppeld was; hoeveel in hun laatste PR-waarneming nog de issuekoppeling
  hadden; en hoeveel zowel als PR als gekoppeld issue in het **laatste
  volledige archiefmoment** werden waargenomen;
- PRs die in het laatste archiefmoment ontbreken (zonder dit als
  gesloten of gemerged te labelen), PRs met eerder waargenomen HEAD-wissels,
  en de historische primaire gateklassen `source`, `failed`,
  `pending`, `changes`, `partial`, `review`, `policy`;
- een maximaal twaalf rijen lange tijdlijn met het **aantal geobserveerde
  issue-gekoppelde PRs per bronmoment** en expliciete melding van
  ontbrekende originele dossierbatches;
- dezelfde samenhangende samenvatting boven de bestaande PR-kaarten in
  scriptvrije offline HTML, plus menselijke Markdown en het
  machineleesbare `issue_focus_summary`-JSON. CSV behoudt zijn
  bestaande bronkolommen en draagt nu per rij ook de complete
  issue-overzichtstellingen.

**Bediening op een bevoegde operatorhost:**

```sh
umask 077
python3 scripts/zsentinel_saved_dossier_timeline.py \
  --source /private/zsentinel/earlier.zip \
  --source /private/zsentinel/later.zip \
  --issue https://github.com/Zennay/zCloud/issues/1518 \
  --format html \
  --output /private/zsentinel/issue-1518-history.html
```

De CSV-versie gebruikt `--format csv --output /private/zsentinel/issue-1518-history.csv`.
De uitvoer blijft private create-only 0600/no-follow in een reeds bestaande
map. De gewijzigde bronnen zijn alleen syntactisch gecontroleerd; geen
QA- of regressiesuites zijn geschreven of gedraaid.

**Belangrijk onderscheid:** dit herstelt niet zelfstandig issue #1518's
live mobiele Activity-feed. Het maakt bestaande opgeslagen PR-/issuebronnen
beter bruikbaar voor een bevoegde eigenaar. Een historische gateklasse,
tijdlijn, verdwenen koppeling, vervallen PR-HEAD of zelfs een recente
archiefwaarneming bewijst **geen actuele verplicht geslaagde CI, review,
actieve claimvrijgave, merge op `main` of live deployment**. Alle
mutatievlaggen blijven `false`; bij iedere daadwerkelijke actie zijn
nieuwe, zelfstandige exact-head GitHub-, Notion-, queue- en releasebewijzen
vereist. De PR is niet DONE zolang GitHub niet `merged=true` meldt en
een eventuele geautoriseerde live-uitrol niet onafhankelijk is vastgesteld.


## 21 oktober · bestaande operatorwerkbak integreert issue-PR herstelgeschiedenis

De `--issue`-selectie in
`scripts/zsentinel_saved_dossier_timeline.py` is opnieuw bruikbaar:
de foutief dubbel ge-escapete reguliere expressie matchte legitieme
GitHub-issue-URLs niet. De herstelde bron accepteert uitsluitend
canonieke `https://github.com/Zennay/<repo>/issues/<nummer>`-adressen
met een begrensde reposlug en issue-identiteit; ongeldige URLs worden
afgewezen.

Dezelfde bekende privé-PR-bronnen kunnen bovendien rechtstreeks worden
meegenomen in de **bestaande interactieve operatorwerkbak**. Het
`--pr-dossier`-argument bestond al; nu benut de hoofdwerkbak de
historische issue-/PR-relaties voor zichtbare herstelbeslissingen.

```sh
umask 077
python3 scripts/zsentinel_operator_workbench.py \
  --board /private/zsentinel/product-board.json \
  --pr-dossier /private/zsentinel/earlier-pr-dossier.zip \
  --pr-dossier /private/zsentinel/later-pr-dossier.zip \
  --output /private/zsentinel/product-recovery-workbench.html
```

**Productgedrag in de scriptvrije bronoverdrachtssectie:**

- Per op het board aanwezig issue zijn de bestaande gekoppelde PRs te
  openen met hun laatst opgenomen PR-HEAD én de HEAD toen de
  issuekoppeling het laatst werd waargenomen. Waargenomen HEAD-wissels,
  issuekoppelingen die verdwenen terwijl de PR wel werd gezien en PRs
  die uit de laatste archiefsteekproef verdwenen krijgen afzonderlijke
  tekst; geen hiervan bewijst merge of issue-afronding.
- Een expliciete **historische bronactualiteit** vermeldt laatst
  opgenomen tijdstip, geschatte leeftijd en ontbrekende ZIP-batches.
  Geen ontbrekende batches gemeld is nooit gelijk aan complete live
  dekking. Onbekende levenscycli blijven ONBEKEND.
- Een uitklapbare projectlijst maakt de gevonden, met het board
  overeenkomende historische PR-relaties per project zichtbaar:
  P0/P1-issues, meerdere bestaande PR-kandidaten, verloren
  issuekoppelingen, afwezige bron-PRs en boardissues zonder opgeslagen
  PR-koppeling. De groepen worden P0-eerst gesorteerd; het maximum
  blijft beperkt tot de reeds toegelaten boardkaarten.
- Deze aanvullende broninformatie gebruikt exact de bestaande
  `build_timeline(...)` en `issue_worklist(...)`-admissie;
  er worden **geen** live providerverzoeken, claim-toewijzingen,
  workflow-reruns, brokerbewerkingen of service-restarts aan de
  operatorwerkbak toegevoegd.

Dit is een historisch **beslisondersteunend** werkoverzicht en geen
autonome productieherstelactie. Vóór daadwerkelijke merge of nieuwe
werkclaim controleert een bevoegd eigenaar actuele issue-/PR-HEAD,
verplichte exacte-head CI, onafhankelijke review, branch protection,
Notion-/queueclaims en productie-releasevoorwaarden. Een historische
PR-verwijzing vervangt geen eigenaarsautorisatie.

De op deze branch gewijzigde Python-modules zijn syntactisch gecontroleerd
op hun exacte gepinde GitHub-bron via verbonden zSSH. Geen nieuwe QA-
of regressiesuites geschreven of gedraaid, geen live deploy uitgevoerd.


### Directe PR-historie bij het geselecteerde issue

De historische PR-verwijzingen waren eerst uitsluitend te vinden in een
afzonderlijke uitklapbare bronsectie bovenaan de werkbak. Bij normale
navigatie op het issue zelf zag de gebruiker alleen de beperkte PR-verwijzingen
uit de originele JSON-boardbron, die juist kan ontbreken of achterlopen.

De huidige productwerkbak projecteert de **reeds onafhankelijk gevalideerde**
PR-dossierinformatie nu ook direct in het interactieve paneel
`Selected product issue`. Zodra een operator een opgeslagen issue kiest,
verschijnt daar (als `--pr-dossier` is meegegeven):

- een beknopte historische triagefocus plus brondekking en het onderscheid
  tussen verdwenen PRs en verdwenen issue-links;
- maximaal twaalf brongebonden bestaande PRs met directe originele
  GitHub-links, laatst waargenomen PR-HEAD versus HEAD bij de laatste
  issuekoppeling, laatst waargenomen gateklasse en aantal HEAD-wissels;
- expliciete afzonderlijke tekst als een issue-link verdween terwijl
  de PR nog in steekproeven voorkwam of als de PR ontbreekt in het laatste
  opgeslagen bronvenster;
- het getelde aantal weggelaten PR-kandidaten als een issue meer dan
  twaalf opgeslagen referenties heeft.

De primaire werkbak en de historische bronnen worden **afzonderlijk**
gevalideerd. De nieuwe HTML gebruikt uitsluitend een kleine, begrensde
JSON-projectie van reeds toegelaten informatie voor de maximaal zestig
geobserveerde boardissues. De tekst wordt via veilige DOM-tekstnodes
geplaatst, zonder externe serviceaanroepen. Het bestaande privébestand
blijft create-only. Ontbreekt een historisch verband, dan zegt het paneel
expliciet dat de relatie **onbekend** is en niet dat de PR is gesloten of
dat het issue vrij kan worden overgenomen.

Deze aanvullende projectie verandert de bestaande handoff-schema's,
operatornotities of importautoriteit niet; de inhoud is uitsluitend voor
menselijke inspectie en vereist vóór daadwerkelijke actie een verse
GitHub-/Notion-/queuebron en alle verplichte releasegates.


## 21 oktober · gefilterde offline PR-herstelwerkbak en één-issue bronoverdracht

De reeds bestaande interactieve zSentinel-werkbak integreert nu de
PR-dossiergeschiedenis **als werkbare triagefunctie**, niet alleen als
een tekstueel archief.

### Historische PR-problemen in dezelfde issue-navigatie

Met `--pr-dossier` verschijnen in de gewone issuefilters de
historische PR-broncategorieën `Saved existing PR reference`,
`Several historical PR candidates`, `PR issue-link disappeared`,
`PR absent from last saved snapshot` en `No saved PR link · UNKNOWN`.
De bestaande prioriteits-, project-, handmatige vervolgstatus- en
boardvergelijkingsfilters blijven gecombineerd werken.

Bij iedere zichtbare issuekaart staat een korte brongebonden aanduiding
van de waargenomen PR-kandidaten, verloren links en verdwenen
bronsteekproeven. Zonder `--pr-dossier` wordt het filter
**uitgeschakeld** met de expliciete melding dat er geen historische
PR-bron werd meegeleverd. Afwezigheid van die bron is nooit bewijs
dat het issue geen bestaande PR of eigenaar heeft.

### Reviewed source-only single-issue PR export

Na selectie van een productissue in het detailpaneel kan een operator
`Review historical PR source handoff (JSON)` kiezen. De eerste klik
toont de volledige, **menselijk controleerbare** inhoud. Pas de
afzonderlijke downloadactie schrijft een browserdownload, voor
uitsluitend het gekozen issue. Bij een andere selectie of gewijzigde
bronprojectie is een nieuwe preview nodig.

Het bronrapport `zsentinel-saved-selected-issue-pr-handoff-v1` bevat
uitsluitend de geregistreerde boardidentiteit/SHA256, het exacte
originele issueadres, het gekozen project, de oorspronkelijke beperkte
archiefbronvensters en hun reviewmanifest-/board-SHA256, alsmede de
maximaal twaalf source-admitted PR-verwijzingen met laatste bekende
HEAD-/issuekoppelings-/gategegevens. Het meldt aanvullend hoeveel
eventuele PR-kandidaten bewust niet in het zichtbare deel zijn
opgenomen. **Andere issues, privéannotaties, handmatige contacten,
workerleasetokens of runtimeclaims worden niet geëxporteerd.**

De export bevat de expliciete, niet-autoriserende waarden
`active_claim_verified=false`,
`exact_head_required_ci_review_verified=false`,
`merged_main_verified=false`, `live_verified=false`,
`claim_authorized=false`, `merge_authorized=false`,
`deploy_authorized=false` en `dispatch_authorized=false`.
Een ontbrekende historische issue-link blijft expliciet UNKNOWN.
De bronprojectie gebruikt de bestaande grens van hoogstens twaalf
archiefbronnen en twaalf getoonde PRs per issue.

De browser werkt lokaal; GitHub, Notion, queue, CI en runner worden
niet aangeroepen, er wordt geen bestaand bestand overschreven en
geen actieve ownerclaim overgenomen. Deze export is een
**historische menselijke overdracht**: voordat een merge, nieuwe
product-PR, claimswitch of productieactie kan plaatsvinden, moeten
exact-head verplichte CI, onafhankelijke review, protected main,
eigenaar/leases en releasebevoegdheid opnieuw live worden vastgesteld.
De gebruiker vroeg geen QA-suites; er zijn er ook geen geschreven
of gedraaid. De bronimplementatie is uitsluitend direct syntactisch
bekeken.


## Single-issue historical PR handoff as readable Markdown

In the existing `--board ... --pr-dossier ... --output ...` workbench, select a
product issue and open **Review human-readable PR handoff (Markdown)**.
The separate **Download reviewed Markdown handoff** control becomes available
only after the displayed document has been reviewed. Switching the selected
issue or changing its historical source invalidates that preview. This is an
additional format beside the existing reviewed JSON receipt, not a replacement
for the machine-readable handoff.

The Markdown is scoped to exactly one selected issue, preserving the exact
original board SHA256 and UTC observation, up to twelve archived ZIP source
windows with original manifest/board SHA256s and missing-batch indexes, and up
to twelve previously linked PRs with saved HEADs, gate classes, disappearing
source links and missing-last-sample warnings. The source triage focus and
human check sequence are readable without opening the whole portfolio. If an
issue has no linked PR in the archived ZIPs, the handoff explicitly marks
the relationship **UNKNOWN**, never PR-free. The optional omitted-reference
count remains visible.

Both the preview and the downloaded `.md` file are assembled entirely in the
offline browser from the *already admitted* source projection and are not
uploaded, sent, or stored automatically. The export excludes local contact
labels, due dates, notes, other issue cards, runtime leases, and any current
worker or release authority. It is **historical evidence**, not fresh GitHub
CI/review/merge/claim proof. Before taking action, a responsible operator
must refresh live GitHub and Notion/queue facts and respect independent review,
exact-head required CI, branch protection, owner consent and release gates.



### Private-by-default portfolio Markdown briefing

The filtered portfolio briefing no longer silently includes locally entered
contact labels, due dates, follow-up decisions, lifecycle timestamps, carried
board references, or operator notes. Open **Preview current filtered briefing**
to review the source-only, default-redacted handoff. Each private field is
explicitly marked `OMITTED (private)` rather than presented as a fabricated
default status.

If those details are necessary for the intended recipient, explicitly check
**Include private operator follow-ups, contact labels and notes** *before*
previewing and downloading. Changing this setting invalidates a prior reviewed
export. The full private handoff remains available by consent, while the
default is safer for passing an issue to a separate project owner.

The historical single-issue Markdown PR receipt is still source-only and
never exports any local private annotations. Both exports remain advisory
offline artifacts: no independently verified GitHub/Notion claim, gate, merge,
or live recovery authority is implied.



### Historical PR ZIP relationships in the reviewed portfolio handoff

The filtered multi-issue Markdown briefing now includes historical
issue-to-PR links from the same independently admitted `--pr-dossier`
archives shown by the selected issue panels. Each report names the ZIP
source observation windows, original review-manifest and board SHA256s,
and any missing source batches. For every included issue it displays
the saved PR candidates, last observed and last issue-linked HEAD,
recorded gate label and HEAD changes, missing final PR samples, lost
issue associations and bounded-reference omissions.

The briefing respects the existing project/priority/issue filters and
does not silently treat an archived PR disappearance as a merge,
a fix, a cleared ownership claim or a live CI result. If no archived
PR source was provided, it says **UNKNOWN**; it does not report a clean
GitHub inventory. It does not perform any external request or mutation.

The private-annotation opt-in remains independent of historical
PR ZIP evidence: history and SHA256 provenance can be shared without
also disclosing local notes or contacts. The original JSON handoff
formats are not changed.



### Saved PR source age is recalculated when opened

The operator workbench may be created while a historical PR ZIP appears fresh
and opened much later, after that label has expired. The selected-issue PR
panel and the multi-issue Markdown owner handoff now re-evaluate the most
recent admitted archive observation against the browser's *current device
clock* whenever they are opened or exported. More than six hours old is
shown as `STALE · REFRESH AUTHORITATIVE SOURCES`; a more-than-five-minute
future observation is shown as a clock/source skew warning, not a
negative age or a valid freshness claim.

This is a **local freshness reminder only**: the browser clock itself is not
trusted authoritative evidence, and even a recently saved ZIP can have stale
GitHub approvals, PR HEADs, issue status or worker leases. Original archive
hashes, timestamps and underlying source admission remain unchanged. There
is no network refresh, privileged action, lease acquisition, CI retry or deploy.



## 21 October · portable, printable recovery worklists without local notes

The existing `scripts/zsentinel_operator_workbench.py --board <saved.json>
--pr-dossier <saved.zip> --output <private.html>` operator page now has **two
standalone, script-free HTML owner handoffs**.

**Selected issue:** choose an issue and use **Preview printable single-issue
PR dossier (HTML)** in the historical existing-PR panel. The preview displays
the original issue link, bounded original board/ZIP archive SHA256 receipts,
archive timestamps and missing batches, up to twelve saved PR references,
historical HEADs, disappearing issue links and the exact next human owner
actions. Only after preview can the operator download the self-contained HTML
page. This is independent of the existing reviewed JSON and Markdown
single-issue exports. No local notes, contact labels, other issue cards or
credentials are carried over.

**Portfolio issue worklist:** use the saved board's **source-only**
filters for project, priority, search, historical PR relationship and original
source comparison, then select **Preview source-only printable worklist**.
Local disposition and follow-up-status filters are deliberately ignored for
this export, preventing the mere inclusion or omission of an issue from
revealing private operator actions to the recipient. The separate owner-oriented page
lists *only* matching saved issues, ordered P0 first, shows original issue and
existing-PR URLs, original archive SHA256s and missing-batch warnings, observed
PR HEAD and gate labels, and the required live approval checks. It reports
how many other source issues were excluded by the chosen filters.
The download button is enabled only after the preview, and any source filter
or selection change requires a fresh preview before download.

Both documents are generated in the browser from the **already independently
admitted** source board and PR ZIPs. They have no JavaScript and use a strict
CSP blocking scripts, images, forms, embeds and network requests. Public issue
titles and other source strings enter HTML exclusively through DOM text nodes,
not executable markup. External canonical GitHub links open only when the
recipient chooses them. No live API calls, new queue task, claim acquisition,
workflow dispatch, CI rerun, merge, service restart or deployment occurs.

The archive-age indicator is recomputed when previewed, but it relies on the
*local device clock*, not a trustworthy live GitHub response. Unobserved
historical PRs, absent links, stale archives, existing checks and missing owner
information remain **UNKNOWN**. Merge and production acceptance require
fresh live source verification, exact-head mandatory CI, independent review,
active owner/lease check, branch protection, verified merge SHA and authorized
release. These portable worklists are never permission to act.



### Reconcile two independently admitted issue-to-PR views

The standalone **single-issue Markdown and printable HTML** receipts now include
**both** separately admitted source perspectives: the original board's at-most
three `open_pr_candidates` with any board-bound sampled exact-head gate
observations, **and** the historical ZIP issue-to-PR timeline with up to twelve
candidates. A PR that appears in the original issue board but not in the
historical ZIP window must not disappear from a handoff merely because that
archive's sample happened to exclude it.

The original board's sampled HEAD and gate labels are explicitly marked as
saved observations; the historical timeline retains its own distinct source
provenance and missing-source warnings. The exports do not coalesce those
observations into one asserted current PR state or treat their absence as
permission to create a competing repair PR. The existing JSON handoff schema
remains unchanged; this is a human-visible source-custody integration.



## Merge-first existing PR reconciliation inside the operator workbench

The offline operator page now includes **Merge-first existing-PR reconciliation
· saved evidence**. Rather than showing the same PR as disconnected issue
cards, it unifies *only the already admitted saved source references* from
the original product issue board and optional historical PR ZIPs. The
source-filtered worklist groups by canonical PR URL and prioritizes the
highest-priority linked issue, P0 first.

Each PR row identifies:

- all linked original product issue URLs, and whether one PR was observed
  against several issues (one candidate for reuse, not proof those issues
  are all fixed);
- whether any one of the linked issues has several existing PR candidates,
  before anyone opens a competing new fix;
- all separately observed board and historical ZIP commit HEADs, with a
  conspicuous **different saved HEAD** warning if source observations vary
  (which can be ordinary history, **not** proof of a code conflict);
- historical PR/issue-reference disappearance, partial ZIP source coverage
  and sampled, non-authoritative gate labels.

A first view shows up to 80 bounded PR rows for operator navigation;
the full reviewed, source-only **PR owner CSV** preserves all PR references
associated with the selected original issue cards rather than silently
dropping less urgent entries. The page also states explicitly how many
saved issues had no PR candidate, how many had alternative PR candidates,
and how many references were omitted from the visible slice.

The worklist intentionally applies **only source filters** from the page,
not locally entered human dispositions, private due dates, follow-up
status, notes or contact labels. No private annotations enter the owner CSV.
The CSV escapes control characters and spreadsheet formula prefixes using
the existing CSV cell encoder. Its per-PR authorization flags explicitly
deny claim, dispatch, CI/review approval, merge and deploy authority.
If source filters or references change after preview, the download is
refused until reviewed again.

A saved PR link or an observed sampled HEAD is **never** evidence that a
PR is currently open, claim-free, approved, merged, released or deployed.
A missing PR in a historical sample is **UNKNOWN**, not resolved. The
actual operator must inspect current GitHub exact-head checks, independent
review, branch-protection status, current Notion/queue worker claims and
release ownership. This panel performs no API call, queue mutation,
automatic worker dispatch, GitHub merge or deployment.



### Explicit owner actions and portable ZIP source custody

The merge-first CSV now carries the **original historical ZIP manifest SHA256s**
instead of asking a recipient to reconstruct the provenance of the PR
candidate list from the browser alone. It also includes an explicit
`archived_gate_batches_missing` marker: an incomplete prior saved batch
never silently appears as an all-clear or approved CI.

Each PR's source-local next step is based on the strongest *unresolved*
observation in a deterministic order: (1) inspect alternative existing
PR candidates and owner claims before duplicate work, (2) refresh current
exact HEAD and required gates where historical heads differ, (3) refresh
PR/issue link where a sampled record disappeared, or (4) check the
original existing PR's current exact-head CI, review and branch protection.
This is a human operator instruction, not an assignment, claim or
permission to merge.

The CSV source scope is still the selected bounded product issue cards
only, and every current authorization flag is false. Saved source changes
may reflect ordinary PR development; differing historical HEADs are
**not** evidence of a content conflict or a new code defect.



### No observed PR is never a clean work queue

The reviewed cross-issue PR CSV includes an explicit **source-only,
P0-first row for every issue with no admitted PR candidate**. Its
`pr_reference_state` is
`not_observed_in_bounded_sources_unknown`, the `pr_url` is empty,
and the original issue URL is retained for the authorized operator to
investigate. This avoids a misleading header-only CSV when a selected
project has real P0 work but no PR was observed in the bounded source.

PR-linked rows remain grouped by existing canonical PR URL. PR-unknown
issues are interleaved by original sampled priority, not silently
appended after lower-priority PRs. All current owner, required CI,
independent review, merge, dispatch and deployment authorization flags
stay explicitly false. An unobserved link never authorizes starting
new work: recheck the **live** GitHub issue/PR inventory and the
current Notion/worker claims first.



## CLI merge-first owner export without opening the workbench

The same P0-first operator concept is now available as a **standalone
source-only offline CSV** for a team that needs a portable artifact without
first opening the browser-based desk:

```sh
python3 scripts/zsentinel_merge_first_owner_csv.py \
  --board /private/saved-product-board.json \
  --pr-dossier /private/older-pr-dossier.zip \
  --pr-dossier /private/newer-pr-dossier.zip \
  --output /private/merge-first-existing-prs.csv
```

The optional `--pr-dossier` can be omitted when only the original issue board
exists. In that case the report still contains board-observed PR links, and
issues without any admitted PR remain explicit **UNKNOWN**, never available
for automatic reassignment or new competing PRs.

This command reuses the workbench's existing stable no-follow board reader
and complete schema/authority validation plus the existing archived-ZIP
admission, chronological source constraints and independent `issue_worklist`
projection. It never queries GitHub, Notion, a worker or a production
database. Up to 60 bounded source issue cards, at most 12 historical ZIP
sources and 900 rendered PR/unknown-issue rows are admitted. The output
limit is one MiB.

The output groups source PR URLs across the selected original issue cards
with P0-first priority, lists all linked issue URLs and titles, observed
original board and archive HEADs separately, saved gate labels and
disappearing-source warnings. Every row includes the original board SHA256,
archived review-manifest SHA256 chain, missing ZIP batch indicator and
the concrete human-only next step. Original P0 issues with no observed PR
are exported as `not_observed_in_bounded_sources_unknown` rather than
disappearing into an empty report.

The destination is a **new** `.csv` file, distinct from all input paths.
The existing create-only private writer produces mode-0600 output and
refuses to overwrite an existing file; no standard output of private
source records occurs. Formula-like source cells are sanitized.
The generated CSV cannot grant owner claims, dispatch, CI, review,
merge, or deploy authorization: those flags are false and must remain so
until separately verified by the authorized human/runtime owner.
This is not a scheduler input or an automated queue writer.

