# zSentinel — geïntegreerde privé-incidentenbalie met worker/Notion-bronnen

Deze productroute hoort bij de bestaande [zSentinel runtime-PR #1563](https://github.com/Zennay/zCloud/pull/1563) en gebruikt het bestaande read-only, eenmalige bronvenster. De interactieve incidentenbalie toont nu naast de menselijke incidentopvolging ook een geïntegreerd **wachtrij-/claimlease-risicobord**, afzonderlijke, uitklapbare **browserworker-eventhistorie** en **Notion-statusobservaties**, zonder een claim of lease uit een van beide bronnen af te leiden.

## Eén opdracht, één privéwerkbank

```sh
umask 077
# NOTION_TOKEN wordt alleen vooraf door de bevoegde eigenaar in de omgeving ingesteld.
python3 scripts/zsentinel_recovery_refresh.py /secure/bronpakket.json \
  --registry projects.json \
  --queue-database /secure/existing-zcloud.sqlite3 \
  --worker-events-database /secure/existing-history.sqlite3 \
  --worker-project cloud \
  --read-notion-claims \
  --notion-data-source 86e406fd-2c99-4ef5-8058-363c1004b3eb \
  --format desk \
  --desk-output /secure/operator-only/zsentinel-workbench-new.html
```

De `--format desk`-route vereist een **nieuw** outputbestand in een bestaande privémap en schrijft geen HTML of Notion-/workergegevens naar stdout. De bestaande create-only privébestandswriter blijft verantwoordelijk voor bestandsrechten en weigert overschrijven, onveilige mappen en symlinks. Het doel mag geen bronpakket, register, ledger, queue-/historydatabase of Notion-snapshot vervangen.

Voor een handmatig opgeslagen Notion-snapshot kan de eigenaar `--notion-claims-export /secure/notion-snapshot.json` gebruiken in plaats van `--read-notion-claims`. Die bron krijgt expliciet het label **handmatig opgeslagen / herkomst niet geverifieerd**. Een geweigerde, gedeeltelijke of afwezige live-bron krijgt een eigen **ONBEKEND**-melding, geen nulclaim.


## Live-cockpit en portfolio met privébronnen

De twee bestaande zelfstandige CLI's volgen nu dezelfde private-output-regel als de refresh-CLI. **Wanneer** een Notion-bron of `--worker-events-database` is ingeschakeld, accepteert `--format html/json/text` uitsluitend een expliciet `--output`-bestand. Een extra `--desk-output`, `--owner-packet-output` of `--handoff-output` is **niet** voldoende: die sidecars schakelen de normale stdout-uitvoer niet uit.

```sh
# Privé HTML met dezelfde read-only brongegevens; geen stdout met inhoud:
python3 scripts/zsentinel_live_cockpit.py \
  --database /secure/existing-zcloud.sqlite3 \
  --project cloud --read-notion-claims \
  --worker-events-database /secure/existing-history.sqlite3 \
  --format html --output /secure/operator-only/live-private-new.html

# Of uitsluitend de interactieve privébalie:
python3 scripts/zsentinel_portfolio_overview.py \
  --database /secure/existing-zcloud.sqlite3 \
  --worker-events-database /secure/existing-history.sqlite3 \
  --format desk --desk-output /secure/operator-only/portfolio-desk-new.html
```

Bij weggelaten gevoelige opt-in bronnen blijft de bestaande openbare, bronbegrensde stdout-route beschikbaar. Deze weigering geldt alleen voor privéstatusdetails. De uitvoerrechten en de bestaande CI-/review-/releasegates veranderen niet.

## Merge-first in dezelfde privéwerkbank

De portfolio-CLI kan nu optioneel een bestaande PR-inspectie rechtstreeks
in dezelfde privécockpit weergeven. De eerder bestaande bronadapter leest
hoogstens drie expliciete of begrensd gevonden PR's en hergebruikt zijn eigen
immutable-HEAD, CI-context, reviews en veilige volgende eigenaarstap.
Dezelfde PR-observatie verschijnt zonder tweede GitHub-GET in de privébalie
en wordt meegenomen in de broncontext-SHA voor exact-source notitie-import.

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --database /secure/existing-zcloud.sqlite3 \
  --window 0 \
  --inspect-pr cloud:1563 \
  --discover-open-prs \
  --format desk \
  --desk-output /secure/operator-only/merge-first-new.html
```

`--inspect-pr PROJECT:NUMBER` moet binnen het gekozen projectvenster vallen.
Ontbrekende review, incomplete exact-HEAD-gates of afgewezen GitHub-bron
blijven menselijke HOLD; deze UI voert zelf nooit een merge uit. Een
afgeronde groene oude commit is geen huidige mergevrijgave.

## Eén privé operatoractiebriefing uit hetzelfde bronmoment

De interactieve incidentenbalie biedt nu **Actieoverdracht voorvertonen**
en daarna **Download privé acties (.md)**. De brongebonden briefing maakt
uit dezelfde al gelezen en gevalideerde gegevens een uitvoerbaar menselijk
overzicht, in deze vaste volgorde: (1) bestaande PR's met exacte HEAD en
ontbrekende merge-/reviewgates, (2) P0-eerst open GitHub-productissues met
open PR-kandidaten, (3) maximaal acht exacte GitHub run/job/HEAD-links en
runnerroute, (4) wachtrijpressure/lease-onzekerheid en de maximaal dertig
hoogste actieve bronincidenten.

Elke briefing draagt de incidentbron SHA-256, eventuele operationele
broncontext SHA-256 en oorspronkelijke observatietijd. De Markdown wordt
in de private HTML ingebed met scriptveilige escaping, uitsluitend lokaal
als tekst voorvertoond en pas na een bewuste tweede klik met een
`Blob`-download aangeboden; er is geen netwerkverzoek, autoretry,
ownerclaim, workflow dispatch of automatische CI-/PR-merge. Dezelfde
incidentenbalie houdt afzonderlijk zijn incidenteigen notitie-export.
Een status `queued` of een ontbrekende bron wordt NOOIT als een gefaalde
job of vrijgegeven werk vertaald.

Dit is géén live autorisatie of volledige portfolio-inventaris. Een
operator controleert alle exacte PR-HEAD-gates, eigenaarclaims, tijdvensters
en relevante P0 veiligheidsbeslissingen opnieuw voordat die handmatig
een herstelactie zou autoriseren.

## Wat de werkbank laat zien

- **Huidige incidentkaarten:** prioriteit, bronstatus, menselijke vervolgstap en lokaal bewaarde notities tot een expliciete privé-export.
- **Bestaande PR's:** éénmalig gelezen exacte PR-HEAD met niet-groene CI, review- en merge/HOLD-richtlijn; bronbeperkingen en geen automatische integratie.\n- **Open productissues:** P0/P1-eerst shortlist uit maximaal acht canonieke GitHub-issuekaarten, met bestaande open PR-kandidaten uit hetzelfde repository, actieve eigenaar expliciet ONBEKEND, en complete bronouderdom/degradatie. Geen QA-only werk of nieuwe claims.\n- **Exacte GitHub-jobroute:** immutable run/job/HEAD-links, vereiste runnerlabels, queued versus waiting/pending, per-job bronmoment en >10-minuten-stale waarschuwing; nooit een automatische CI-, merge- of runnerdiagnose. De aanvullende discovery toont alleen de expliciet begrensde workflowvensters.\n- **Geblokkeerde werkregels:** apart P0-eerst status=blocked-overzicht uit dezelfde observatie, maximaal veertig getoonde project-/prioriteitcombinaties, onderscheid tussen complete/partiële/verouderde/ontbrekende bron. Een geblokkeerde status is geen bewezen workflowfout of retrytoestemming.\n- **Wachtrijdruk:** toont uit één bestaande SQLite-observatie aantallen wachtend/bezet, herhaalde claimpogingen (≥3 en ≥10), verlopen leases, onbekende leaseklokken en inconsistente claimvensters. Tot 18 project/prioriteitgroepen worden risicogericht gerangschikt. Verouderde/onvolledige bron verbergt projectdetails en wordt nooit als actuele vrijgave voorgesteld; geen extra SQLite-transactie.\n- **Workerpaneel:** index-gedragen recente eventtijdlijn en veilige eigenaaropvolging voor maximaal drie gekozen projecten. `generation-progress` is browsersignaal, geen onafhankelijke bewijsvoering dat het werk werkelijk doorgaat.
- **Notionpaneel:** maximaal dertig gesamplede `Claimed`, `Running` en `Verifying`-werkregels met originele bronstatus en afkappingswaarschuwing; labels zijn geen bewijs van actuele queue- of file-scope-eigendom.

De privé-overdracht is nu **op de volledige geselecteerde operationele broncontext gebonden**: de SHA-256 van de bestaande GitHub-, queue-, worker- en Notion-gegevens wordt bij de incidentfingerprint meegenomen. Nieuwe gegevens met dezelfde incident-ID's kunnen daardoor een oud exact-source-notitiebestand niet ongemerkt als actuele opvolging importeren. De operator kan oude notities alleen via de reeds bestaande expliciete **Herbruik oude notities**-route dragen, die nieuwe bronbeoordeling vereist. Bij invalidatie van bronautoriteit, discrepante status of misvormde brongegevens weigert de renderer de werkbank vóór het bijwerken van het optionele incidenteledger.

## Bevoegdheid en oplevering

Dit is een **privé operatorproduct** en geen live autonome hersteldienst. Alle claim-, queue-, lease-, CI-retry-, worker-resume-, GitHub-dispatch-, service-, release- en deployrechten blijven **false**. Een werkitem op `Running` betekent niet dat een pad wordt bezet door die Notionregel; een afwezige regel geeft geen vrijgave.

Code staat uitsluitend op de bestaande zSentinel-PR-branch totdat de actuele exacte HEAD door verplichte GitHub-checks, onafhankelijke review en branch protection komt en bevoegd wordt gemerged. Geen QA-/regressie-/testsuites toegevoegd of handmatig uitgevoerd.
