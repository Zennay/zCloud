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

## Wat de werkbank laat zien

- **Huidige incidentkaarten:** prioriteit, bronstatus, menselijke vervolgstap en lokaal bewaarde notities tot een expliciete privé-export.
- **Exacte GitHub-jobroute:** immutable run/job/HEAD-links, vereiste runnerlabels, queued versus waiting/pending, per-job bronmoment en >10-minuten-stale waarschuwing; nooit een automatische CI-, merge- of runnerdiagnose. De aanvullende discovery toont alleen de expliciet begrensde workflowvensters.\n- **Geblokkeerde werkregels:** apart P0-eerst status=blocked-overzicht uit dezelfde observatie, maximaal veertig getoonde project-/prioriteitcombinaties, onderscheid tussen complete/partiële/verouderde/ontbrekende bron. Een geblokkeerde status is geen bewezen workflowfout of retrytoestemming.\n- **Wachtrijdruk:** toont uit één bestaande SQLite-observatie aantallen wachtend/bezet, herhaalde claimpogingen (≥3 en ≥10), verlopen leases, onbekende leaseklokken en inconsistente claimvensters. Tot 18 project/prioriteitgroepen worden risicogericht gerangschikt. Verouderde/onvolledige bron verbergt projectdetails en wordt nooit als actuele vrijgave voorgesteld; geen extra SQLite-transactie.\n- **Workerpaneel:** index-gedragen recente eventtijdlijn en veilige eigenaaropvolging voor maximaal drie gekozen projecten. `generation-progress` is browsersignaal, geen onafhankelijke bewijsvoering dat het werk werkelijk doorgaat.
- **Notionpaneel:** maximaal dertig gesamplede `Claimed`, `Running` en `Verifying`-werkregels met originele bronstatus en afkappingswaarschuwing; labels zijn geen bewijs van actuele queue- of file-scope-eigendom.

De privé-overdracht is nu **op de volledige geselecteerde operationele broncontext gebonden**: de SHA-256 van de bestaande GitHub-, queue-, worker- en Notion-gegevens wordt bij de incidentfingerprint meegenomen. Nieuwe gegevens met dezelfde incident-ID's kunnen daardoor een oud exact-source-notitiebestand niet ongemerkt als actuele opvolging importeren. De operator kan oude notities alleen via de reeds bestaande expliciete **Herbruik oude notities**-route dragen, die nieuwe bronbeoordeling vereist. Bij invalidatie van bronautoriteit, discrepante status of misvormde brongegevens weigert de renderer de werkbank vóór het bijwerken van het optionele incidenteledger.

## Bevoegdheid en oplevering

Dit is een **privé operatorproduct** en geen live autonome hersteldienst. Alle claim-, queue-, lease-, CI-retry-, worker-resume-, GitHub-dispatch-, service-, release- en deployrechten blijven **false**. Een werkitem op `Running` betekent niet dat een pad wordt bezet door die Notionregel; een afwezige regel geeft geen vrijgave.

Code staat uitsluitend op de bestaande zSentinel-PR-branch totdat de actuele exacte HEAD door verplichte GitHub-checks, onafhankelijke review en branch protection komt en bevoegd wordt gemerged. Geen QA-/regressie-/testsuites toegevoegd of handmatig uitgevoerd.
