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
