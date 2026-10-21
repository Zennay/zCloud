# zSentinel · één commando voor privé-eigenaaroverdracht

Deze toevoeging is onderdeel van de **bestaande** [zCloud PR #1563](https://github.com/Zennay/zCloud/pull/1563), niet een zelfstandige scheduler of productie-release.

## Wat er daadwerkelijk bijgekomen is

De bestaande live cockpit kan, naast de normale HTML-, tekst- of JSON-weergave, in **dezelfde eenmalige bronlezing** een aparte, private `.md`- of `.json`-eigenaaroverdracht aanmaken. De operator ontvangt een P0-eerst actielijst die strikt onderscheid maakt tussen:

1. verse, exacte incidenten uit de bestaande herstelrouter;
2. owner-review voor toegewezen workers met een relevante pauze, lease-onzekerheid of browserblokkadesignaal uit de **optionele** geïndexeerde SQLite-workerbron;
3. open P0/P1-productissues uit de begrensde bestaande issue-inbox.

Elk item bevat het project, de categorie, volgende **menselijke** controle, en expliciet `claim_authorized=false`, `retry_authorized=false`, `merge_authorized=false`, `deploy_authorized=false`. Onvolledige bronnen worden nergens tot `healthy`, `free` of `recovered` gepromoveerd. Maximaal 24 items worden opgenomen; afkapping wordt zichtbaar aangegeven. De bron levert geen taakclaim, dispatch of vrijgave.

## Eén bronmoment; twee offline resultaten

Op een **reeds geautoriseerde** host met bestaande lokale queue-DB en een **vooraf aangemaakte privémap**:

```sh
umask 077
python3 scripts/zsentinel_live_cockpit.py \
  --database /pad/naar/bestaande/history.db \
  --project cloud --project supa \
  --worker-events-database /pad/naar/bestaande/history.db \
  --handoff-output /private/zsentinel/handoff-20261020.md \
  --format html > /private/zsentinel/cockpit-20261020.html
```

Het P0-eerst-overzicht is ook **zonder** aparte export direct in de offline HTML op te vragen:

```sh
umask 077
python3 scripts/zsentinel_live_cockpit.py \\
  --github-only --project cloud \\
  --show-owner-handoff --format html > /private/zsentinel/p0-werkbak.html
```

Dit creëert geen eigen handoff-file vanuit Python; de HTML-redirect is een bewuste operatorhandeling naar een al bestaande besloten map. De bronverzameling blijft dezelfde eenmalige, begrensde GitHub-only read; queue- en actieve claimstatus blijven in deze variant ONBEKEND. `--show-owner-handoff` vereist expliciet `--format html`.

Bij `--format html` verschijnt dezelfde P0-eerst-werkbak bovendien **boven in de bestaande offline cockpit**, met directe links naar canonieke bestaande productissues. De privé-export en de HTML-kop delen exact dezelfde in-memory bronbeslissing: er komt geen tweede GitHub-, Notion- of SQLite-lezing bij. Onbetrouwbare titels, notities en actietekst worden HTML-geëscaped en bij Markdown ook letterlijk gecodeerd, zodat externe brontekst niet als opmaak, HTML of actieve link kan worden geïnterpreteerd.

De HTML-redirect is een expliciete shellhandeling van de **operator** en krijgt alleen private permissies wanneer de operator zelf een private `umask`/map gebruikt. Voor de **door het programma geschreven** handoff gelden onafhankelijk `O_EXCL | O_NOFOLLOW`, reguliere 0600-bestandspermissies en een bestaande oudermap. De handoff weigert een bestaande bestemming en een pad dat samenvalt met een SQLite-bron, lokale dedup-ledger, opgeslagen Notion-export, vorige privéscan of andere privé-uitvoer.

Machineleesbare uitvoer is ook mogelijk:

```sh
python3 scripts/zsentinel_live_cockpit.py \
  --github-only --project cloud \
  --handoff-output /private/zsentinel/handoff-20261020.json \
  --format text
```

Zonder worker-DB blijft workerstatus `not_requested`; zonder queue-DB blijft queue-status `not_requested_or_unavailable`. Het is dan **niet** bewezen dat er geen claims of lopend werk zijn. Notion-status is hooguit een partiële indicator en bewijst geen actuele file-claim of geldige lease. Bestaande echte CI, onafhankelijke review, exact-head SHA en beschermde main blijven verplicht.

## Gecombineerd portfolio-overzicht: dezelfde P0-eerst actiewerkbak

Dezelfde brongebonden eigenaaroverdracht is nu ook optioneel geïntegreerd in
`scripts/zsentinel_portfolio_overview.py`, naast het bestaande P0/P1-issuebord,
PR-gates, broncoverage en workeractiviteit:

```sh
umask 077
python3 scripts/zsentinel_portfolio_overview.py \\
  --github-only --window 0 --show-owner-handoff \\
  --format html > /private/zsentinel/portfolio-met-owner-handoff.html
```

Het globale portfolio houdt alle canonieke projecten in het bestaande dashboard,
maar **de eigenaaroverdracht omvat alleen het geselecteerde GitHub-venster** en
de werkelijk opt-in gelezen lokale bronnen; andere projecten staan niet
bewijsbaar op groen. De extra HTML-kaarten gebruiken exact dezelfde reeds
opgehaalde `decision` en geen extra providerrequests. Er vindt ook hier geen
claim-, merge-, queue- of releasewrite plaats; het nieuwe opt-in vlagargument
wordt bij niet-HTML-uitvoer geweigerd.

## 21 oktober · bestaand PR-werk direct in de centrale eigenaaroverdracht

Bij de portfolio-CLI wordt de optionele eigenaaroverdracht nu **ná** de al
bestaande begrensde PR-observatie samengesteld. Daarmee komen open product-PR's
niet meer uitsluitend in het afzonderlijke PR-paneel terecht: de gedeelde
HTML-, JSON-, Markdown- en tekstoverdracht toont voor maximaal drie gekozen
PR's een directe bestaande PR-link, de geobserveerde bronstatus en de concrete
volgende eigenaaractie. Is de exact-head check- en reviewbron compleet genoeg
waargenomen, dan bevat JSON ook de geobserveerde volledige HEAD SHA en alle
vier expliciet onware claim-/retry-/merge-/deploy-toestemmingen.

De UI noemt daarnaast aantoonbaar geobserveerde falende, wachtende en
onbesliste check-runs uitsluitend als **ondergrenzen van de GitHub-steekproef**,
en het aantal in hetzelfde head-venster aangetroffen onafhankelijke approvals.
Bij een onvolledige bron ontbreken deze cijfers bewust; nul waargenomen
approvals is nooit voldoende reviewbewijs. Vereiste checks en branchregels
blijven expliciet **niet geverifieerd**.

Voor de portfolio-CLI, met `--window` ingesteld op het betreffende projectvenster:

```sh
umask 077
python3 scripts/zsentinel_portfolio_overview.py \
  --github-only --window 0 \
  --inspect-pr cloud:1563 --show-owner-handoff \
  --format handoff > /private/zsentinel/pr-eigenaaroverdracht.md
```

Dit voorbeeld veronderstelt dat venster 0 `cloud` bevat; kies anders het
betreffende venster. Een optionele `--discover-open-prs` of
`--inspect-referenced-prs` hergebruikt dezelfde bronkoppeling; de handoff
start geen extra API-reads bovenop de **expliciet geselecteerde** PR-inspectie.
Zonder optionele PR-inspectie is `coverage.prs = not_requested`, nooit
"geen PR's". Bij een gewijzigde PR-head, ontoegankelijke GitHub-bron of
gedeeltelijke bron toont de kaart uitsluitend een menselijke broncontrole:
geen afgeleide CI-goedkeuring en geen onbetrouwbare HEAD.

Prioriteiten blijven P0-eerst. Echte geobserveerde PR-reparaties staan vóór
nieuwe P1-productissues binnen dezelfde ernst; wachtende review/CI krijgt P2
en verdringt dus geen verse P0/P1-herstelactie. GitHub-meta- en reviewbronnen
bewijzen nooit de volledige branchbescherming. De operator moet op het actuele
exacte commitmoment required checks, onafhankelijke reviews, actieve owners en
mergebevoegdheid controleren. `source_committed` of `mergeable=true` betekent
hier uitdrukkelijk niet `merged_main` of `live_verified`.

## Beveiliging en release

- De samenstelling is lokaal, onveranderlijk, éénmalig en zonder provider-I/O; alle netwerk-GETs komen uitsluitend uit de al bestaande, begrensde live cockpit.
- De handoff verwerkt alleen begrensde velden en canonieke zCloud/GitHub issue-links, geen workerprompt, browsertab, secret, queue-ID, ruwe claimtoken of pad van een lokaal bestand.
- Handmatige overdracht mag géén automatische claim/runner-restart/retry/billing/merge/deploy activeren.
- Bij ontbrekende of inconsistent geclassificeerde bronnen faalt de handoff conservatief en schrijft geen geldig verklaarde werkbak.
- `PR open` is geen `main merged`. Deze implementatie mag uitsluitend via de bestaande verplichte exacte-head CI, onafhankelijke review, branch protection en geautoriseerde runtime-release naar productie.

## Implementatie

- `scripts/zsentinel_runtime_handoff.py` — bronvalidatie, P0/P1-triage en Markdown/JSON-rendering.
- `scripts/zsentinel_live_cockpit.py` — nieuwe optionele `--handoff-output`, padisolatie, veilige create-only export; bestaande HTML/JSON/tekstworkflow intact.
- `scripts/zsentinel_private_output.py` — reeds bestaande, hergebruikte private 0600-uitvoer; **niet** aangepast.

## 20 oktober · exacte jobnavigatie en eigenaarstermijn

De P0/P1-incidentkaarten tonen bij een actueel door de router erkend
github_exact_run_job-bewijs de volledige GitHub-run-ID, job-ID en
40-tekens HEAD SHA. De live cockpit en portfolio-overview geven hun
canonieke projects.json-repositorymapping mee aan de kaartopbouw;
daardoor zijn de Exacte job- en Exacte commit-links direct aanklikbaar
in de offline HTML en leesbaar in privé-Markdown. JSON bevat dezelfde
canonieke bronvelden voor handmatige triage. Onbekende jobidentiteit
blijft afwezig: een projectnaam alleen leidt nooit tot een link.

Een incidentkaart toont ook review_by_utc: de oorspronkelijke
target_review_by uit de herstelbeslissing, in UTC.
Bij gelijke ernst en kaartsoort sorteert de werkbak op de eerst
verstrijkende deadline. Een al gepasseerde deadline krijgt zichtbaar
DEADLINE VERSTREKEN volgens de lokale exportklok, zonder automatische melding. Dit is een
menselijke beoordelingsdeadline, geen herinnering of bewijs van
acceptatie. Ongetekende of naïeve tijdstempels falen conservatief;
jobvelden vereisen positieve integer-ID's en een volledige lowercase SHA.

Ook de standaard tekstuitvoer vermeldt voor een vers incident de exacte run/job/HEAD, en de standaard incidentele HTML-kaarten (live cockpit én portfolio) tonen de bronidentiteit met directe job-/commitlinks zonder dat de optionele handoff-kaart hoeft te worden ingeschakeld. Een bron zonder geverifieerde job-ID blijft linkloos.

De links zijn uitsluitend navigatie naar bronbewijs. Er is geen
GitHub-mutatierecht, Notion-claim, VPS-, database- of workeractie
toegevoegd. Claim/retry/merge/deploy blijven uitdrukkelijk niet
geautoriseerd. De bestaande PR #1563 vereist vóór levering naar main
exacte-head verplichte CI, onafhankelijke review en branch protection.

## 20 oktober · historische blokkades in dezelfde eigenaaroverdracht

**Probleem:** de huidige eenmalige bronlezing is per definitie partieel.
Een reeds gedetecteerde menselijke blokkade kan uit het actuele GitHub-
of queuevenster verdwijnen zonder dat die echt is hersteld. De bestaande
herstelrouter bewaart daarom al een aparte privéledger en levert maximaal
vijf menselijke historische hercontrolekandidaten. Tot nu ontbraken die in
de **eigenaaroverdracht**, waardoor de operator buiten de speciale
historische cockpit onbedoeld een incompleet actueel actielijstje zag.

Ook de standaard offline incidentcockpit geeft historische kaarten nu directe links naar de oude exacte GitHub-job en commit, mits de historische projectidentiteit, veilige run/job/SHA en canonieke repositorymapping allemaal overeenkomen. De kop blijft nadrukkelijk HISTORISCH · GEEN ACTUELE BRON; klikken is uitsluitend bekijken en vormt geen runner-, job- of bronherstelbewijs.

De bestaande --handoff-output en --show-owner-handoff nemen vanaf nu
**zonder extra providerlezingen of dispatch** een tweede, gescheiden
sectie op met historische, nog niet gereconcilieerde hercontroles.
Daarbij wordt het incident-ID teruggebonden aan de door de router
samengestelde, begrensde historische ledgerbron (maximaal 30 rijen).
De combinatie van project, oorspronkelijke ernst, reden, laatst gezien,
bronstatus en exacte GitHub-run/job/SHA moet overeenkomen. Tegenstrijdige
of onbevoegd makende bronnen worden geweigerd.

- **Live P0/P1-items** blijven een eigen prioriteitenrij. Historische
  items komen **niet** terecht in actuele incidenten, notificaties,
  nieuwe hersteltriggers of de lijst van automatisch te claimen taken.
- **HISTORISCH · NIET ACTUEEL WAARGENOMEN** staat herkenbaar als
  afzonderlijk kopje in privé-HTML en Markdown; JSON heeft apart
  historical_owner_rechecks. Een per-bron UTC-laatst-gezien-moment en
  optionele canonieke oude job/commit-links helpen de eigenaar gericht
  te hercontroleren.
- Maximaal vijf relevante oude hercontroles worden getoond. De
  afkappingsindicator maakt expliciet dat verborgen oude incidenten
  **niet** als hersteld mogen worden beschouwd. De output blijft
  volledig alleen-lezen en attribueert geen actuele claim/eigenaar.
- Het bestaande privé-HTML/JSON/Markdown-uitvoerbeleid, 0600
  bestand, bescherming tegen output-overlap en het verbod op
  automatische reruns/merges/deploys blijven hetzelfde.

**Bediening:** gebruik de eerder beschreven eenmalige cockpit met
--state (een bestaande privé-incidentledger) en --handoff-output
of --show-owner-handoff. Zonder bestaande historische ledger ontstaan
geen verzonnen historische kaarten. Een menselijke operator controleert
de huidige exacte GitHub-job en eigenaar apart, vóór elke actie.

Dit is **source-implementatie binnen open PR #1563**; nog geen main
merge of productie-integratie. Verplichte exacte-head CI, onafhankelijke
review, protected-main en eigenaar-/runtimegates blijven intact.

## 20 oktober · queue #677: eigenaarcheck voor onbetrouwbare leasevensters

De bestaande éénmalige portfolio- en queue-trend-CLI leest reeds echte
zCloud-queuegegevens vanuit een SQLite read-only snapshot, zonder
productiewrites. De bronobserver onderscheidt een verlopen lease,
een onbekende vervaltijd en een **tegenstrijdig leasevenster**
(ontbrekende/onmogelijke claimstart of claimstart niet vóór vervaltijd).
De aparte eerdere claimtrend-vergelijking in
`scripts/zsentinel_queue_churn.py` verloor die derde categorie.
Een actuele onbetrouwbare lease kon daardoor ten onrechte ontbreken in
de handmatige vergelijkingsweergave wanneer geen nieuwe claimpogingen
hadden plaatsgevonden.

Deze bestaande productbron is uitgebreid met drie samenhangende acties:

1. **Bronopname weigert inconsistentie** tussen totalen, projectgroepen en
   status-specifieke voorbeelden: de drie leaseflags zijn wederzijds
   uitsluitend en mogen niet voorkomen op ongeclaimde `queued` taken.
   Eerdere privé-snapshots zonder de nieuwe expliciete bronvelden zijn
   niet automatisch vergelijkbaar en worden veilig afgewezen.
2. **Vergelijking verliest conflicten niet**: de nieuwste huidige bron
   rapporteert `current_inconsistent_lease_windows_owner_review`,
   zichtbare veranderingen op groepsniveau en een afgekapt privéoverzicht
   van getroffen project/prioriteit/status. Dit gebeurt ook wanneer de
   claimpogingenteller niet steeg. De originele queue-ID, leaseklokken,
   claimeigenaar en claimtoken blijven uit operatorbestanden.
3. **Concreet operatorresultaat**: standaard portfolio-HTML,
   tekst/Markdown en privéscherm vermelden het aantal onbetrouwbare
   leasevensters. Het privéscherm toont afzonderlijke projectkaarten met
   de opdracht aan de bevoegde eigenaar om claimstart en expiry in de
   oorspronkelijke bron te verifiëren. Dat is géén automatische vrijgave,
   retry of workerovername.

Gebruik het bestaande commando om een eerste **privé, create-only**
snapshot te maken, en vergelijk deze binnen 72 uur met een latere bron:

```sh
python3 scripts/zsentinel_queue_churn.py \
  --database /pad/naar/bestaande/history.db \
  --snapshot-output /eigenaar/prive/queue-eerst.json
python3 scripts/zsentinel_queue_churn.py \
  --database /pad/naar/bestaande/history.db \
  --previous /eigenaar/prive/queue-eerst.json \
  --output /eigenaar/prive/queue-verandering.html \
  --format html
```

Gebruik alleen een door de eigenaar bestaande afgeschermde map.
Eenmalige broninspectie op de verbonden zSSH-host zag acht toegewezen
actieve queue-items, vijf met minstens drie claimpogingen, maar geen
huidig tegenstrijdig of verlopen leasevenster op het waarnemingsmoment.
Dat bewijst geen nul problemen buiten deze bronsteekproef.
De zCloud-runnerlistener bleek daarnaast `active (running)`, terwijl
exacte GitHub-jobs nog `queued` waren. Geen restart onder hoge swapdruk
of met andere actieve projectworkers; het GitHub-runnerroute/registratie-
onderzoek uit #1326 blijft een onafhankelijke eigenaarstaak.

**Release:** dit is broncode uitsluitend op bestaande [PR #1563](https://github.com/Zennay/zCloud/pull/1563);
geen native writer-migratie, geen nieuwe automatische retry/backoff policy
uit [issue #677](https://github.com/Zennay/zCloud/issues/677), geen
productiedeploy. Verplichte exacte-head CI en onafhankelijke review
blijven vereist voor de merge naar main.

## 20 oktober · integratieherstel: leaseconflict tot in centrale eigenaarwerkbak

De portfolio-CLI bouwde de optionele `--show-owner-handoff` **voordat**
een `--queue-previous` privésnapshot met de actuele queue was
vergeleken. De aparte queue #677-kaart kende daardoor wel
`inconsistent_owner_lease_window_rows`, maar de eigenlijke centrale
eigenaaroverdracht werd stilzwijgend zonder die blokkades gerenderd.
Dit is nu een gecorrigeerd **productdatapad**, geen extra QA-gate.

- `scripts/zsentinel_portfolio_overview.py` bouwt éénmaal de
  eigenaaroverdracht **nadat** de brongebonden claimtrend in dezelfde
  `decision` is opgenomen. Er komen geen extra provider- of
  databaselezingen, queueclaims, retries of notificaties bij.
- `scripts/zsentinel_queue_churn.py` biedt één gedeelde,
  read-only validator voor de private lijst van maximaal twintig
  geobserveerde project-/prioriteit-/status-conflicten. Dezelfde
  broncontrole voedt de bestaande losse HTML en de geïntegreerde
  eigenaaroverdracht; ruwe queue-ID's, leasetokens en namen worden
  nooit toegevoegd.
- `scripts/zsentinel_runtime_handoff.py` koppelt de trendsamenvatting
  aan precies hetzelfde `decision`-document voordat brongebonden
  `queue_lease_window_review`-kaarten naar de bestaande
  P0/P1/P2-werkbak gaan. De kaart noemt het geobserveerde project,
  oorspronkelijke prioriteit, status en pogingen, plus de verplichte
  actie: **originele claimstart en vervaltijd bij de bevoegde eigenaar
  controleren, zonder vrijgave of overname**.
- Het privé HTML/Markdown-rapport vermeldt nu afzonderlijk de
  vergeleken queuebron en het totale aantal conflicten. Zonder
  `--queue-previous` staat er expliciet **ONBEKEND** in plaats
  van nul; een gedeeltelijke of afgeknotte steekproef blijft zichtbaar.
  JSON behoudt dezelfde provenance- en autorisatievelden.

**Bediening:** de bestaande portfolio-CLI met
`--database /pad/naar/bestaande/history.db`,
`--queue-previous /eigenaar/prive/queue-eerst.json` en
`--show-owner-handoff --format html` geeft de centrale
eigenaarwerkbak mét de private trendinformatie. De vorige snapshot
moet eerst via het bestaande alleen-lezen queue-snapshotcommando zijn
opgeslagen. Bij ontbrekende, tijdsverkeerde of niet bij dezelfde
database horende bron blijven de gates en onbekendstatus zichtbaar.

**Levering:** commits op de reeds openstaande
[zCloud PR #1563](https://github.com/Zennay/zCloud/pull/1563).
Dit is geen `main`-merge of productie-uitrol. Exacte-head CI,
review, branch protection en uitvoerrechten blijven onverminderd gelden.

## 20 oktober · eerste-run actuele queueblokkades in eigenaaroverdracht

De volledige `--show-owner-handoff` is nu ook bruikbaar bij een
**eerste run zonder eerdere privésnapshot**. De bestaande brongebonden
router levert vanuit precies dezelfde SQLite `mode=ro` /
`query_only`-waarneming al een gevalideerde, maximaal achttien
project-/prioriteitsgroepen tellende actuele queuebron. Deze wordt in
`scripts/zsentinel_runtime_handoff.py` nu direct aangesloten op de
bestaande P0/P1/P2-eigenaarwerkbak, zonder extra providercalls of
query's uit het rapportsysteem.

Per daadwerkelijk **actueel** waargenomen projectgroep met
tegenstrijdige claimklokken, verlopen leases of ontbrekende
leaseverificatie komt een `queue_current_owner_review`-kaart.
Een kaart bevat alleen project, oorspronkelijke prioriteit,
geobserveerde geaggregeerde risicotellers en het UTC-bronmoment;
geen taakidentificator, claimtoken, leasegegevens, owner-secret,
vrije queue-titel of automatiseringsautorisatie.
De vervolgstap is handmatig: oorspronkelijke claim en vervaltijd
door de bevoegde eigenaar verifiëren. Ook een verlopen lease is
**geen** toestemming om werk opnieuw te claimen.

Bij `--queue-previous` wordt bovendien de al bestaande private
claimsnapshotvergelijking verwerkt als **andere** bronklasse
(`queue_lease_window_review`), met uitdrukkelijke broncontrole.
De private HTML/Markdown-werkbak maakt het verschil tussen
`Queue huidig` en `Queue #677 vergelijking` zichtbaar.
Bij `--github-only`, ongeldige of verouderde bronstatus is het
resultaat expliciet **ONBEKEND**, nooit een gefingeerde nulmeting.
Een afgekapt projectvenster vermeldt dat niet-getoonde gevallen
niet zijn beoordeeld.

Met de bestaande portfolio-CLI:
```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --database /pad/naar/bestaande/history.db \
  --show-owner-handoff --format html
```
De optionele `--queue-previous /eigenaar/prive/queue-eerst.json`
levert daarbovenop de afzonderlijke trendinformatie. Alle
bronvergelijkingen blijven read-only en nieuw aangemaakte
privérapporten blijven aan het bestaande create-only/0600-pad
onderworpen.

**Releasestatus:** uitbreiding op bestaande [PR #1563](https://github.com/Zennay/zCloud/pull/1563),
nog niet gemerged of deployed. Geen QA-, regressie- of testsuites
geschreven/gestart; geen claims, wachtrij- of runnerwijzigingen.
Verplichte exacte-head CI, onafhankelijke review en toestemming
voor productie-inzet blijven intact.

## 20 oktober · eigenaaroverdracht in alle bestaande portfolio-uitvoerformaten

Het bestaande portfolio-commando accepteert `--show-owner-handoff`
nu niet alleen bij `--format html` maar óók bij
`--format handoff`, `--format text` en `--format json`.
De source-bound `build_handoff` is éénmaal opgebouwd nadat
de queue-vergelijking (indien beschikbaar) en de actuele
SQLitestatus al bekend zijn; formats renderen **hetzelfde**
read-only advies en voeren geen extra observaties of mutaties uit.

- **HTML**: bestaande optionele eigenaarpaneel boven de actuele cockpit.
- **handoff (Markdown)**: complete afzonderlijke centrale
  eigenaarsbriefing vóór de bestaande blocker-first briefing.
- **text**: dezelfde veilige eigenaarsbriefing vóór de
  bestaande compacte console-portfolioinformatie.
- **JSON**: expliciet `owner_handoff`-veld met geobserveerde
  actieve leases, optionele historische/private trends, bronbereik
  en per-item `claim_authorized=false`,
  `retry_authorized=false`, `merge_authorized=false`,
  `deploy_authorized=false`. Zonder opt-in is het veld `null`.

De formaten `ics` en `desk` blijven uitgesloten van deze
optie. Het oorspronkelijke bron-/outputbeleid blijft gelden:
uitsluitend geautoriseerde privérapportlocaties, geen verzending,
workerclaim, automatische issue-sluiting, retry, merge of deploy.
Een ontbrekende `--queue-previous` blijft expliciet **ONBEKEND**,
terwijl de eerste-run-actuele leasebron wel al zichtbaar kan zijn.

Voorbeeld voor een private handmatige overdracht:
```sh
python3 scripts/zsentinel_portfolio_overview.py \
  --database /pad/naar/bestaande/history.db \
  --show-owner-handoff --format handoff \
  --output /eigenaar/prive/zsentinel-overdracht.md
```

Alles is broncode op de reeds bestaande
[PR #1563](https://github.com/Zennay/zCloud/pull/1563).
Geen nieuwe QA-suite, workflow, procesgate of productieactie.

## 21 oktober · actuele VPS-capaciteit in de bestaande eigenaarwerkbak

De herstelrouter leverde al een brongevalideerde `vps_resource_admission` op basis van de **optioneel expliciet gevraagde read-only VPS-metingen**. Deze beslisinformatie werd nog niet in de centrale `--show-owner-handoff`-prioriteiten weergegeven. Een schijf boven de bestaande 90%-drempel of zeer lage swapruimte kon daardoor in de queue/CI-overdracht onzichtbaar blijven.

De bestaande eigenaarhand-off maakt van **uitsluitend** een bronvastgestelde `state=overloaded` nu één extra **P1 cloud / vps_capacity_owner_review**-kaart. Die heeft een directe link naar bestaand [P0-runnerincident #1326](https://github.com/Zennay/zCloud/issues/1326), vermeldt disk- en swaprisico vooraan, bevat alle bekende reden-codes als begrensde JSON-velden en eindigt bij de geautoriseerde host-/runner-eigenaar. De centrale `coverage.vps_capacity` onderscheidt voortaan `overloaded`, `stale`, `unavailable` en `advisory_headroom`; oude/ontbrekende metingen worden **nooit** tot groen resultaat of afgerond herstel verheven. De kaart wordt via dezelfde bronbeslissing vanzelf weergegeven in bestaande HTML-, Markdown-, tekst- en JSON-handoffs. Er is geen extra `statvfs`, SQL-leesactie of netwerkoproep nodig.

Ongewijzigde veiligheid: `claim_authorized=false`, `retry_authorized=false`, `merge_authorized=false`, `deploy_authorized=false`. **Geen automatische opruiming, swapclear, service-restart, workerstop, opslagmigratie of capaciteitswijziging.** Een gesignaleerde bottleneck bewijst ook niet welke GitHub-job onassigned is. De release blijft bij exacte CI-HEAD, review en de bestaande productie-eigenaar; bron op de bestaande open [PR #1563](https://github.com/Zennay/zCloud/pull/1563), niet live of `main`.

## 21 oktober · onverzoende incidentafhankelijkheden zichtbaar in eigenaaroverdracht

Een incidenteel uit de begrensde bron verdwenen job is **niet** bewezen
opgelost. De herstelrouter stelt een taak met `depends_on_incidents`
voortaan alleen als onafhankelijk werk voor wanneer **elke** expliciete,
canonieke incident-ID op basis van diezelfde originele run/job/HEAD
in het privéledger als `exact_job_completed_source_reconciled` staat.
Een ontbrekend, nog geblokkeerd of slechts heringedeeld runnerincident
blijft een HOLD; onbekende IDs worden nooit als vrij beschouwd.

De bestaande centrale `--show-owner-handoff` en de aparte
`--handoff-output` tonen deze status in dezelfde bronbeslissing zonder
extra GitHub-, Notion- of SQLite-verzoeken: `incident_dependency_owner_summary`
bevat brondekking en exact het aantal vastgehouden kandidaat-taken. De
HTML-kop en Markdown geven ook bij ontbrekende bron **ONBEKEND** weer, nooit
nul. Tot acht P0-eerst `incident_dependency_owner_hold`-kaarten laten de
betrokken taak/projectcombinatie zien en vragen uitsluitend de oorspronkelijke
eigenaar om bron-/claimreconciliatie. Raw incident-IDs worden niet naar het
HTML-paneel gekopieerd. Geen workerclaim, autoretry, vrijgave of merge.

De router en werkbak wijzigen geen operationele taak-/queue-toestand; dit
zijn leesbare veiligheids-HOLDs op bestaande productvoorstellen. Het werk
staat op de reeds open PR #1563; onafhankelijke review, exacte-head CI en
beschermde main zijn nog verplicht vóór integratie. Geen nieuwe QA-tests.


## 21 oktober · onvolledige afhankelijkheidsbron is geen nulmeting

De bestaande router kan alle werkvoorstellen onderdrukken wanneer de taak- of
claiminventaris ontbreekt, ouder is dan vijf minuten of een actieve claim geen
bekende bestandsscope heeft. In die situatie is het waargenomen aantal
dependency-HOLDs **geen totaal**: de centrale eigenaaroverdracht toont
`incomplete_or_stale` en **ONBEKEND** in plaats van `0` alsof alle
afhankelijkheden zijn beoordeeld. Dezelfde status verschijnt in de standaard
tekstuitvoer en offline HTML. Ook oudere opgeslagen beslissingen zonder
dependency-coverageveld worden fail-closed als onbekend weergegeven.

Alleen `coverage=complete` staat toe dat de handoff een concreet totaal presenteert
van verder routeerbare wachtrijtaken die door incidentafhankelijkheden worden
tegengehouden. Al geclaimde/actieve, niet-geschikte, menselijk afgeschermde of
taken zonder geldige bestandsscope vallen buiten deze kandidaat-telling. Deze status is gebaseerd op expliciete complete
inventarissen, verse observatietijden en bekende actieve claimscopes; het blijft
advies en verleent geen claim-, retry-, merge- of deployrecht. De implementatie
staat op de bestaande PR #1563 en vereist nog steeds de originele exacte-head
CI, onafhankelijke review en protected-main release.


### Bronoorzaak bij onbekende routering

Bij `incomplete_or_stale` geeft dezelfde beslissing nu begrensde reden-codes
mee: taak- of claiminventaris niet expliciet compleet, ontbrekende of te oude
waarnemingstijd, of een actieve claim zonder bekende bestandsscope. De
centrale HTML- en Markdown-eigenaaroverdracht vertaalt die codes naar
concrete bronproblemen; het standaard tekstpaneel doet hetzelfde. Een ontbrekende
oude coverage-sectie blijft generiek **ONBEKEND**. De compatibiliteitsproperty
`claim_inventory_complete` blijft bestaan, terwijl
`routing_inventory_complete` de gecombineerde verse taak-/claimtoelating
expliciet maakt.


## 21 oktober · router-uitvoergates nu onderdeel van de centrale eigenaarwerkbak

De herstelrouter produceerde al `routing_gate_holds` wanneer een op zichzelf
beschikbare taak niet uitvoerbaar was door (1) een expliciet geblokkeerde
GitHub/self-hosted gate, (2) ontbrekende of onvoldoende bewezen VPS-capaciteit,
of (3) een niet-bewezen uitvoeringsbestemming. De centrale eigenaaroverdracht
toonde die routerbeslissing nog niet. Hierdoor zag een operator in de losse
router wel een HOLD, maar ontbrak dezelfde relevante taak in de gezamenlijke
`--show-owner-handoff`-werkbak.

De gedeelde `build_handoff()` zet maximaal acht brongebonden voorbeelden om
naar `routing_gate_owner_hold`-kaarten, sorteert ze P0-eerst tussen bestaande
incident- en afhankelijkheidswerkorders, en bewaart de veilige instructie per
reden. Alle outputvormen die deze gedeelde eigenaarwerkbak consumeren
(HTML/Markdown/JSON/tekst) krijgen daardoor dezelfde kaarten zonder een tweede
API- of SQLite-lezing. JSON bevat daarnaast
`routing_gate_owner_summary` met de gecertificeerde dekking, een alleen bij
complete inventaris betrouwbaar HOLD-totaal, en voorbeeldafkapping; offline
HTML en Markdown zetten dit totaal ook om naar een menselijk leesbare melding.

**Betekenis van de cijfers:** `complete` bewijst uitsluitend hoeveel
*verder geschikte kandidaat-taken* binnen het begrensde routervenster aan
deze drie uitvoergates werden tegengehouden. Het bewijst niet dat er geen
andere taken bestaan, of dat een gebruiker kan dispatchen. Bij verouderde,
ontbrekende of gedeeltelijke taak-/claimbronnen is het totaal **ONBEKEND**,
niet nul. Tegenstrijdige gaten, ongeldige taken, herhaalde voorbeelden en
actievlaggen anders dan `false` worden door de werkbak geweigerd.

Er is ook een bronsyntaxfout verholpen in de reeds bestaande
dependency-HOLD-actie: een ontbrekende Python-stringconcatenatie tussen het
taaklabel en het aantal open incidentafhankelijkheden. Voorafgaande bron
kon daardoor niet worden geïmporteerd; de huidige exacte commit wordt
alleen via bronparse gecontroleerd, niet via een QA-suite.

**Operator:** hercontroleer originele claim-/lease-eigenaar, exacte
GitHub-status, self-hosted bron en benodigde uitvoeringsbestemming. Geen
automatische claim, dispatch, runnerrestart, CI-rerun, merge of deploy vanuit
deze lokale werkbak. De nieuwe inhoud staat voorlopig uitsluitend op
[zCloud PR #1563](https://github.com/Zennay/zCloud/pull/1563) en moet door de
gebruikelijke exacte-head CI, onafhankelijke review, branchbescherming en
runtime-releasegates.


## 21 oktober · historische herhaalde HOLD-episodes blijven zichtbaar

Bij het vergelijken van twee tot acht brongebonden routingvensters worden herhaalde
HOLDs nu ook bewaard wanneer het laatste venster de taak niet opnieuw bemonstert
of de HOLD-reden tussentijds is gewijzigd. Eerder verdwenen geldige
drie-of-meer-vensterreeksen tot nu toe uit het overzicht doordat alleen de
**laatst actieve** reeks werd geëxporteerd.

- De bronreeks moet nog altijd dezelfde geverifieerde opaque queue-origin hebben,
  strikt chronologisch zijn en per paar maximaal 72 uur verschillen. Een gap,
  redenwissel of overgang naar **suggestion only** sluit een episode af.
- Elke episode draagt het eerste en laatste 1-gebaseerde bronvenster,
  oorspronkelijke UTC-bronstempels en het aantal opeenvolgende waarnemingen.
  De rendering verifieert de exacte bronvensterpositie, de canonieke UTC-
  stempels en overeenstemming tussen zichtbare voorbeelden, episode-totaal en
  afkappingsvlag. Onvolledige of tegenstrijdige episode-export wordt geweigerd
  in plaats van als geldig eigenaarbewijs gepresenteerd.
  De nieuwe booleaan `observed_in_latest_sample` zegt alleen dat deze exacte
  HOLD in het laatste beperkte bronvenster **voorkwam**. Ook `true` is geen
  bewijs van voortdurende blokkade of huidige claimeigenaar.
- HTML en tekst tonen expliciet **HISTORISCH · NIET IN LAATSTE STEEKPROEF · NIET
  OPGELOST** waar toepasselijk. CSV gebruikt voor dergelijke episodes
  `REPEATED_HOLD_HISTORICAL` en correcte historische begin-/eindvensters in
  plaats van een onjuist eindvenster op het actuele bronmoment.
- Meerdere episodes van één taak blijven apart wanneer de bronreeks tussentijds
  brak. P0 gaat voor P1/P2; bij afkapping blijft het totale aantal episodes
  bewaard en meldt elke mensleesbare export zichtbaar dat voorbeelden ontbreken.
- Dit betreft de reeds bestaande `scripts/zsentinel_route_history.py` en
  `scripts/zsentinel_route_continuity.py`-invoer, zonder aanvullende API-/SQLite-
  leesoperaties, QA-suites, claim-, retry-, runner-, provider-, merge- of deployactie.

**Operatoractie:** open de oorspronkelijke eigenaar/lease/PR/CI-bron opnieuw
voordat je conclusies trekt. Een historische HOLD-kaart is een hercontrole,
geen toestemming voor vrijgeven, werk claimen of een CI-run starten.
Dezelfde protected-main, verplichte exact-head CI, onafhankelijke review en
release-owner gates blijven van kracht.

## 21 oktober · Unicode-veilige bronlabels in runtime-ownerhandoff

De gecombineerde runtime-ownerhandoff gebruikt één bounded tekstnormalisatie voor
actie, type en bronlabels. Onzichtbare Unicode-format controls (waaronder bidi-
en zero-width-markeringen), Unicode-regel-/alineascheidingstekens en niet-ASCII-
witruimte worden als zichtbare `⟦U+XXXX⟧`-markeringen getoond. C0/C1-control-
en surrogate-codepunten blijven zichtbaar in plaats van onopgemerkt door te
lekken; tab, CR en LF uit gewone meerregelige bronnotities worden eerst gewone
scheidingsspaties. De Markdown-renderer escaped bovendien pipes en
strikethrough-delimiters, zodat providertekst geen extra tabelkolommen of
doorgestreepte statuslabels kan injecteren.

Dit is uitsluitend een render-/handoff-integriteitsmaatregel. De bron, prioriteit,
lease, owner-claim, CI-status, recoverybeslissing en release-autorisatie worden
niet gewijzigd. Bestaande CI en menselijke/branch-protectiongates blijven intact.
