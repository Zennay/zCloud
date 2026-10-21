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

## Privé actieoverdracht zonder browser

Alledrie de bestaande read-only CLI's accepteren nu optioneel
`--action-output /bestaande/prive-map/NIEUW.md`. Deze schrijft **naast**
het gekozen operatorformaat exact één create-only, 0600 Markdown-actiebriefing
uit **hetzelfde** in-memory bronmoment, zonder extra GitHub-/Notion-/SQLite-GET,
queuewrite, eigendomsovername of notificatie.

```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --database /secure/existing-zcloud.sqlite3 --window 0 \
  --inspect-pr cloud:1563 --discover-open-prs \
  --format desk --desk-output /secure/operator-only/desk-new.html \
  --action-output /secure/operator-only/actions-new.md

python3 scripts/zsentinel_live_cockpit.py \
  --database /secure/existing-zcloud.sqlite3 --project cloud \
  --format desk --desk-output /secure/operator-only/live-desk-new.html \
  --action-output /secure/operator-only/live-actions-new.md

python3 scripts/zsentinel_recovery_refresh.py /secure/bronpakket.json \
  --registry projects.json \
  --format desk --desk-output /secure/operator-only/refresh-desk-new.html \
  --action-output /secure/operator-only/refresh-actions-new.md
```

Het nieuwe .md-doel mag nooit gelijk zijn aan bron- of state/ledgerbestanden,
het HTML-resultaat, eerdere privébestanden of een ander outputpad. De bestaande
private-output writer weigert een bestaand bestand, symlink of onveilige map.
Bij optionele Notion- of workerdata blijft een expliciete privé `--output` of
`--format desk --desk-output` verplicht; `--action-output` alleen
onderdrukt de normale HTML/JSON/text stdout **niet**. De uitvoer bevat
alleen handmatige adviezen met onzekere lease, owner en CI-gates expliciet
onbewezen.

In zowel de browserpreview als de headless `--action-output` staan voor elke
bestaande PR de leesbare voorgestelde eigenaaractie, expliciete HEAD,
mislukte/wachtende/onbesliste checknamen, klassieke statuscontexten en
waargenomen onafhankelijke reviewtelling. Afzonderlijke branch protection
en niet-gescande verplichte checks blijven altijd **NIET BEWEZEN**.

## Betrouwbare actuele versus historische broninformatie

De HTML-actieoverdracht en de create-only CLI-briefing hanteren nu
expliciete bronouderdom per afzonderlijke bron, onafhankelijk van de
algemene incidentklok:

- **Exacte GitHub-job**: na tien minuten expliciet historisch; een
  `waiting`/`pending` job wordt niet gediagnosticeerd als kapotte
  self-hosted runner. Het advies wordt dan een nieuwe exacte joblezing.
- **Notion**: een handmatig opgeslagen connector-snapshot is nooit live.
  Alleen een recente token-gedreven statuswaarneming mag als beperkte
  actieve-statusondergrens worden aangeduid; ook dan is geen filelease
  of eigenaarschap bevestigd.
- **Browserworker-events**: een bron ouder dan tien minuten toont geen
  oude `safe_followup` meer als actuele eigenaaractie. Eerst bron en
  queuelease verversen, zonder workerherstart.
- **Queue**: actuele bron vereist geldige bronleeftijd van hoogstens vijf
  minuten en complete inventaris. Oude of onvolledige aantallen worden
  als eerder waargenomen geëtiketteerd.
- **Geblokkeerde werkregels**: dezelfde privéoverdracht groepeert
  `status=blocked` apart op P0/P1/P2/P3, met maximaal twintig
  huidige project/priority-groepen. Een stale of incomplete bron laat
  geen oude individuele groepen als actuele taken zien. Deze status
  bewijst nooit een technische fout of toestemming tot retry.

Ook wanneer het offline HTML-bestand lang in een browsertabblad open
blijft, werkt de lokale incidentbron-ouderdomswaarschuwing bij.
De **privé actieoverdracht** krijgt bij elke preview een prominente
actuele klokwaarschuwing vóór het gecompileerde bronmateriaal. Als
de ouderdomscategorie tussen preview en download verandert, is een
nieuwe bronvoorvertoning vereist. Dit is enkel een lokale labelcontrole:
geen achtergrond-fetch, Notion-query, workflowdispatch of claim.

## Herstelde tekstuitvoer

De live-cockpit accepteerde reeds `--format text --output NEW.txt`,
maar gebruikte vervolgens abusievelijk het writerformaat `text` in
plaats van `txt`. De private create-only writer accepteert
uitsluitend echte bestandsextensies. Deze route schrijft nu daadwerkelijk
de gevraagde `.txt` via dezelfde eigenaar- en no-follow-bescherming.
De `--format desk` en JSON/HTML-bestanden blijven ongewijzigd.

## Wat de werkbank laat zien

- **Huidige incidentkaarten:** prioriteit, bronstatus, menselijke vervolgstap en lokaal bewaarde notities tot een expliciete privé-export.
- **Bestaande PR's:** éénmalig gelezen exacte PR-HEAD met niet-groene CI, review- en merge/HOLD-richtlijn; bronbeperkingen en geen automatische integratie.
- **Open productissues:** P0/P1-eerst shortlist uit maximaal acht canonieke GitHub-issuekaarten, met bestaande open PR-kandidaten uit hetzelfde repository, actieve eigenaar expliciet ONBEKEND, en complete bronouderdom/degradatie. Geen QA-only werk of nieuwe claims.
- **Exacte GitHub-jobroute:** immutable run/job/HEAD-links, vereiste runnerlabels, queued versus waiting/pending, per-job bronmoment en >10-minuten-stale waarschuwing; nooit een automatische CI-, merge- of runnerdiagnose. De aanvullende discovery toont alleen de expliciet begrensde workflowvensters.
- **Geblokkeerde werkregels:** apart P0-eerst status=blocked-overzicht uit dezelfde observatie, maximaal veertig getoonde project-/prioriteitcombinaties, onderscheid tussen complete/partiële/verouderde/ontbrekende bron. Een geblokkeerde status is geen bewezen workflowfout of retrytoestemming.
- **Wachtrijdruk:** toont uit één bestaande SQLite-observatie aantallen wachtend/bezet, herhaalde claimpogingen (≥3 en ≥10), verlopen leases, onbekende leaseklokken en inconsistente claimvensters. Tot 18 project/prioriteitgroepen worden risicogericht gerangschikt. Verouderde/onvolledige bron verbergt projectdetails en wordt nooit als actuele vrijgave voorgesteld; geen extra SQLite-transactie.
- **Workerpaneel:** index-gedragen recente eventtijdlijn en veilige eigenaaropvolging voor maximaal drie gekozen projecten. `generation-progress` is browsersignaal, geen onafhankelijke bewijsvoering dat het werk werkelijk doorgaat.
- **Notionpaneel:** maximaal dertig gesamplede `Claimed`, `Running` en `Verifying`-werkregels met originele bronstatus en afkappingswaarschuwing; labels zijn geen bewijs van actuele queue- of file-scope-eigendom.

De privé-overdracht is nu **op de volledige geselecteerde operationele broncontext gebonden**: de SHA-256 van de bestaande GitHub-, queue-, worker- en Notion-gegevens wordt bij de incidentfingerprint meegenomen. Nieuwe gegevens met dezelfde incident-ID's kunnen daardoor een oud exact-source-notitiebestand niet ongemerkt als actuele opvolging importeren. De operator kan oude notities alleen via de reeds bestaande expliciete **Herbruik oude notities**-route dragen, die nieuwe bronbeoordeling vereist. Bij invalidatie van bronautoriteit, discrepante status of misvormde brongegevens weigert de renderer de werkbank vóór het bijwerken van het optionele incidenteledger.

## Bevoegdheid en oplevering

Dit is een **privé operatorproduct** en geen live autonome hersteldienst. Alle claim-, queue-, lease-, CI-retry-, worker-resume-, GitHub-dispatch-, service-, release- en deployrechten blijven **false**. Een werkitem op `Running` betekent niet dat een pad wordt bezet door die Notionregel; een afwezige regel geeft geen vrijgave.

Code staat uitsluitend op de bestaande zSentinel-PR-branch totdat de actuele exacte HEAD door verplichte GitHub-checks, onafhankelijke review en branch protection komt en bevoegd wordt gemerged. Geen QA-/regressie-/testsuites toegevoegd of handmatig uitgevoerd.

## GitHub-bronweigering, schijnbaar vastgelopen jobs en veilige overdracht

De geïntegreerde `zsentinel_recovery_refresh.py` leest uitsluitend bestaande
vastgepinde GitHub Actions-runs en jobs (maximaal acht paren; maximaal zestien
GET-verzoeken). Bij een HTTP 401 (`authentication_required`) of een door GitHub
bevestigde uitgeputte rate-limit (`rate_limit_confirmed`) stopt de ververser
met verdere verzoeken voor hetzelfde token. Ook nog niet bevraagde jobrecords
worden als **gedegradeerd en ONBEKEND** gemarkeerd; oude succes-, faal- en
runnerreceipts worden niet hergebruikt als nieuwe observatie. Een HTTP 403 zonder
bewezen rate-limit is juist een **projectgebonden**
`forbidden_or_rate_limit_unknown`: dit bewijst geen accountbrede billingfout,
en andere repositorybronnen hoeven niet automatisch geblokkeerd te worden.

Een privé JSON-uitvoer maakt `requests_attempted_actual`,
`requests_attempted_max`, `provider_admission_denial`,
`skipped_due_to_provider_denial` en per project de vaste
`admission_category` zichtbaar. De bestaande offline HTML-cockpit toont
expliciet de verantwoordelijke menselijke herstelactie en het aantal overgeslagen
bronnen. Zonder geldige run/job-evidence kan geen incident worden verheven tot
geverifieerde runnerstoring of afgerond werk.

Daarnaast weigert de collector een tegenstrijdige joblifecycle: een nog
wachtende of draaiende job met een finale conclusie, een voltooiingstijd bij
niet-voltooide status, of jobtijden in de verkeerde volgorde. Toegestane
`started_at`- en `completed_at`-waarden staan alleen als bronmetadata in
privé JSON; ze bewijzen geen scheduler- of workerherstel. GitHub kan tussen het
run-GET en het job-GET van status veranderen. In dat geval moet de operator
later **een nieuw bronmoment** ophalen, niet op basis van de oude snapshot
herstarten of CI opnieuw starten.

Ook de bestaande brongebonden ownerhandoff neemt een echte GitHub 401 op als
**P1 GitHub-tokenherstel voor de bevoegde eigenaar**. Een 403 zonder
bewezen quota-uitputting krijgt maximaal drie projectgebonden P1-kaarten
voor repo-/toegangscontrole. Een bevestigde 429 is een bronwachtstatus:
zichtbaar in de cockpit, maar **geen nieuw menselijk alarm** en geen
reden om runner/CI te herstarten. Dat voorkomt dat acht mislukte GETs
acht schijnincidenten veroorzaken.

De herstelroute geeft op basis van deze observaties nooit zelfstandig claims,
GitHub-reruns, runner-restarts, Notion-acties, betaalde capaciteit, merges of
deploys vrij. Bevoegde eigenaren en exacte-head CI/review blijven de enige
operationele poorten.


## 21 oktober · bestaande PR’s direct in herstel- en eigenaarsoverzicht

Dezelfde `scripts/zsentinel_recovery_refresh.py` heeft nu één expliciete
**merge-first** ingang die de reeds aanwezige read-only GitHub
`discover_open_prs`- en `observe_prs`-collectors hergebruikt.
Er wordt geen nieuw PR gemaakt, geen GitHub-workflow gestart en geen eigenaarschap
overgenomen. De operator kiest maximaal drie bestaande product-PR’s; bij
projectdiscovery wordt maximaal één venster van twintig meest recent gewijzigde
open PR’s per geselecteerd project bekeken. QA-only PR’s worden door de
bestaande productfilter uitgesloten. De source van maximaal drie exacte PR’s
wordt vervolgens gecontroleerd op HEAD, gesamplede checks, klassieke statussen,
reviews, draft en bekende platformweigeringen. Deze momentopname bewijst
**nooit** dat alle verplichte branch protection-gates of huidige actieve claims
voldaan zijn.

```sh
umask 077
python3 scripts/zsentinel_recovery_refresh.py /secure/bronpakket.json \
  --registry projects.json \
  --read-existing-prs \
  --existing-pr cloud:1563 \
  --pr-project cloud \
  --read-product-issues --issue-project cloud \
  --format desk \
  --desk-output /secure/operator-only/herstel-en-pr-werkbank.html \
  --owner-handoff-output /secure/operator-only/pr-herstel-handoff.md
```

De `--existing-pr PROJECT:NUMBER`-optie pinnt een bekende PR als eerste;
`--pr-project PROJECT` gebruikt **alleen resterende capaciteit** voor
begrensde open-PR-discovery. Beide opties vereisen `--read-existing-prs`.
Zonder expliciete PR/projectkeuze gebruikt deze modus de eerste maximaal drie
canonieke projecten uit hetzelfde al ingelezen bronpakket als de router.
Dat blijft een *steekproef*: ontbrekende PR’s, updates buiten het venster en
actieve fileclaims zijn ONBEKEND. Geen gegenereerd advies verleent een merge.

Naast de offline `desk`-HTML bevat de reguliere `--format html`-cockpit nu
hetzelfde exacte PR-paneel. Bij `--format text` wordt een brongebonden PR
vervolgactie toegevoegd; `--format json` bewaart het volledige
`pr_delivery`-object. `--owner-handoff-output` maakt een afzonderlijk
**nieuwe** privé-`.md`, `.json` of `.csv` aan op basis van diezelfde
router en PR-bron, zonder extra GET. De eigenaarsexport bevat P0/P1
incidenten, onafhankelijke werkblokkades, open PR-gates en menselijke
vervolgacties in dezelfde prioritering. Een eigenaar kan de export bekijken
en bevoegd handmatig opvolgen; deze wordt niet automatisch als queue of
workerinput gebruikt.

Voor `--read-existing-prs` is `--output` verplicht bij
`html`/`text`/`json`; bij `--format desk` is de private
`--desk-output` verplicht. Anders wordt de opdracht geweigerd voordat
PR-titels, reviews of exacte commitinformatie naar stdout kunnen gaan.
Alle uitvoerbestanden gebruiken bestaande 0600 create-only veilige opslag,
met cross-output/source/ledger-aliaschecks; eerdere handoffbestanden worden
niet overschreven. Incomplete PR-discovery of providerweigering blijft
expliciet onvolledig; nul geselecteerde PR’s bewijst geen leeg backlog.

Een integratie- of live-release mag pas na **exact-head verplichte CI,
afzonderlijke review, beschermde branch en bevoegde owner/releaseactie**.
`merge_authorized=false`, `deploy_authorized=false` en onvolledige claimdekking
blijven behouden.


## Eén providerweigering geldt voor het hele gekozen bronvenster

Bij een **verifieerbare** GitHub-401 of API-quota-uitputting in de
vastgepinde run/job-verversing worden de latere optionele issue- en
PR-collectors voor datzelfde token niet alsnog afzonderlijk bevraagd.
`issue_source=degraded_skipped_after_provider_denial` geeft expliciet
nul vervolgreads door deze collector aan, **niet nul issues**.
Expliciet opgegeven `--existing-pr PROJECT:NUMBER` blijft als veilige
navigeerbare PR-link bewaard met `next_step=verify_pr_source_with_owner`,
maar HEAD, review en CI-status worden niet verzonnen. Niet ontdekte
PR’s blijven onbekend; de private HTML/JSON-handoff toont dit apart.
Een GitHub-403 zonder bewezen globale limiet wordt niet portfolio-breed
geblokkeerd: andere repositorybronnen kunnen wel onafhankelijk beschikbaar
zijn. Deze logica wijzigt geen GitHub-ratelimits, kosten, claimleases,
worker-, scheduler- of deploygedrag.


Wanneer de **issuecollector zelf** pas een bevestigde globale rate-limit
tegenkomt, geeft deze dezelfde stop door aan de latere PR-observer.
Ook de open-PR-discovery mag een bevestigd rate-limit-signaal doorgeven,
waarna de maximaal drie exacte PR-GET-routes worden overgeslagen.
HTTP-401/403 die de issuecollector aan één repository toewijst blijft
bewust **projectgebonden**, zodat bereikbare andere projecten niet
stilvallen. Een wegens tokenweigering geheel overgeslagen PR-waarneming
heeft geen nieuw `observed_at_utc`-GitHub-bronmoment: de bewaarde
PR-link is slechts een menselijke navigatieaanwijzing.


## 21 oktober · merge-first rechtstreeks in de LIVE-herstelcockpit

De bestaande **one-command** `scripts/zsentinel_live_cockpit.py` kan nu
met `--inspect-existing-prs` exact dezelfde PR-bron en menselijke
release-informatie opnemen als de bundle-gebaseerde refresh-workbench.
Er is **geen vooraf handmatig opgebouwd evidence-JSON** vereist:

```sh
umask 077
python3 scripts/zsentinel_live_cockpit.py \
  --database /secure/zcloud-existing.sqlite3 \
  --registry projects.json \
  --project cloud \
  --inspect-existing-prs \
  --existing-pr cloud:1563 \
  --pr-project cloud \
  --format desk \
  --desk-output /secure/operator-only/live-incident-en-pr-new.html \
  --handoff-output /secure/operator-only/live-pr-eigenaar-new.md
```

Een eigenaar zonder SQLite-database kan in plaats van `--database`
expliciet `--github-only` kiezen; de output vermeldt dan dat queue- en
claimzekerheid **ONBEKEND** is. Dat verleent nooit een vervangende
runner-/release-/queue-toestemming.

De operator kan maximaal drie bestaande product-PR’s kiezen met
`--existing-pr PROJECT:NUMBER`; iedere PROJECT moet eveneens bij
`--project` zijn geselecteerd. `--pr-project PROJECT` voegt hoogstens
één bestaand, begrensd **open PR-venster per project** toe en gebruikt alleen
overgebleven ruimte tot de drie geselecteerde PR’s. Zonder expliciete
PR-pins/projecten gebruikt de CLI de al gekozen 1–3 `--project`-waarden.
De bestaande QA-only-filter blijft actief, zonder nieuwe QA uit te voeren.
`--inspect-existing-prs` is niet combineerbaar met
`--skip-github-issues` of ICS-als-hoofdformaat.

De reguliere privé-HTML toont een exacte PR-ownerkaart met commit,
CI- en reviewstatus, onvolledige bron en de volgende menselijke actie;
`--format desk` hergebruikt hetzelfde PR-receipt. De
`--handoff-output` en `--show-owner-handoff` nemen het identieke
PR-receipt mee in de P0-eerst runtime-ownerhandoff. JSON bevat
`pr_delivery`, en private tekstuitvoer noemt de merge-first
vervolgstappen. De bestaande begrensde verzamelaar verricht geen
GitHub-dispatch, claim, merge, branch-update of deploy.

Een duidelijke GitHub-401 of **geverifieerde** quota-uitputting tijdens
de exacte jobverversing stopt ook onnodige issue-/pin-GETs en latere
PR-GETs binnen dezelfde runtime-aanroep. Bevestigde globale quota-uitputting
in de issuecollector stopt de latere PR-reads. Een repositorygebonden
403/401 zonder bewezen globale limiet wordt niet ten onrechte als
algemeen kosten- of runnerincident gepresenteerd. Alle niet opgehaalde
issues, PR’s en jobs blijven **ONBEKEND**, nooit "nul" of vrijgegeven.

**Privé-uitvoer is verplicht:** met `--inspect-existing-prs`
moet HTML/JSON/tekst naar een nieuw `--output`-bestand, of gebruikt
`--format desk` een nieuw `--desk-output`-bestand.
`--handoff-output` alléén onderdrukt gewone stdout **niet**.
De bestaande 0600 create-only writer weigert overschrijven.
Een ontbrekende independent review, incomplete exact-head CI, actieve
claim en branch protection blijven onverkort echte release-HOLDs.
Deze bronuitbreiding is geen live recovery- of deploybewijs.


## 21 oktober · issue → bestaande PR, exact dezelfde bron in eigenaarshandoff

De privé `zsentinel_runtime_handoff.build_handoff(..., pr_desk=...)`
koppelt nu `open_product_issue`-kaarten **uitsluitend** aan de
bestaande geobserveerde, canonieke PR’s uit hetzelfde huidige
`pr_delivery`-venster (hoogstens drie). De correlatie is repo-gebonden:
een issue-referentie naar een andere repository wordt geweigerd.
Het systeem voert geen extra GitHub GET uit en maakt geen nieuwe PR.

Wanneer een issue naar één van deze reeds geïnspecteerde PR’s verwijst,
toont de eigenaarkaart het PR-URL, de **daadwerkelijk geobserveerde exacte
HEAD** als die betrouwbaar bestaat, het bronbereik en de volgende
brongebonden menselijke stap (bijvoorbeeld huidige CI herstellen,
onafhankelijke review aanvragen, of toegang herstellen). Bij mislukte
GitHub-admission blijft de HEAD expliciet **ONBEKEND**. Niet
geïnspecteerde issue-referenties blijven als ongetoetste links zichtbaar;
het aantal ontbrekende exacte PR-inspecties wordt ook vastgelegd.

De gecombineerde signalen verschijnen in de scriptvrije HTML-eigenaarstrip,
in de Markdown-handoff en als gestructureerde, begrensde velden in JSON.
De CSV houdt bewust exact dezelfde **CSV-v1 kolommen** voor bestaande
importeurs, maar noemt binnen `next_step` ook de brongebonden gekoppelde
PR-actie; `existing_pr_urls` behoudt de navigatielinks.
Een gekoppelde open PR betekent **niet** dat het issue is opgelost,
dat de huidige eigenaar/claim bekend is of dat merge/deploy mag.
Het P0/P1 issue blijft zichtbaar naast de PR-ownerkaart, zodat
beveiligings-/infrastructuurblokkades nooit als afgevinkt verdwijnen.

Geen queueclaim, CI-rerun, PR-merge, workflowdispatch, accountbetaling,
runnerrestart of live deploy wordt door deze eigenaarsoverdracht uitgevoerd.
